"""Tests for src/clouddog/retrievability.py (the pull-back prover).

Unit tests run against a scripted local HTTP server (no network): the
module's _resolve_asset_infos is monkeypatched to point at the fake
server, so the ranged-download / retry / resume / hash logic is exercised
for real without touching GitHub.

The live test (test_live_prove_real_release_asset) pulls a REAL small
release asset from mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant and is
skipped unless CLOUDDOG_LIVE_TEST=1.
"""

from __future__ import annotations

import hashlib
import http.server
import os
import re
import shutil
import socket
import threading
from typing import ClassVar

import pytest

from clouddog import retrievability
from clouddog.retrievability import (
    ProofResult,
    _AssetInfo,
    prove_registry_entry,
    prove_retrievable,
)

PAYLOAD = bytes((i * 7919) % 256 for i in range(100 * 1024))  # 100 KiB deterministic
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


class _ScriptedHandler(http.server.BaseHTTPRequestHandler):
    """Serves PAYLOAD with Range support; failures are scripted per test."""

    payload = PAYLOAD
    failures: ClassVar[list] = []  # consumed in order: int status | "drop" | "empty"
    accept_ranges = True
    server_version = "TestProver/1.0"

    def log_message(self, *args):
        pass

    def _send_body(self, start: int, status: int) -> None:
        body = self.payload[start:]
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if self.accept_ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header(
                "Content-Range", f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}"
            )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.failures:
            mode = self.failures.pop(0)
            if mode == "drop":
                # Abrupt mid-stream close: partial bytes, then EOF.
                half = len(self.payload) // 2
                self.send_response(200)
                self.send_header("Content-Length", str(len(self.payload)))
                self.end_headers()
                self.wfile.write(self.payload[:half])
                self.wfile.flush()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.close_connection = True
                return
            if mode == "empty":
                self.send_response(200)
                self.send_header("Content-Length", str(len(self.payload)))
                self.end_headers()
                self.wfile.flush()
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.close_connection = True
                return
            self.send_response(int(mode))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start = 0
        range_hdr = self.headers.get("Range")
        if range_hdr and self.accept_ranges:
            m = re.match(r"bytes=(\d+)-", range_hdr)
            if m:
                start = int(m.group(1))
        if start >= len(self.payload):
            self.send_response(416)
            self.end_headers()
            return
        self._send_body(start, 206 if start else 200)


@pytest.fixture()
def fake_server():
    _ScriptedHandler.failures = []
    _ScriptedHandler.accept_ranges = True
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ScriptedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join()


def _ref(url: str, size: int = len(PAYLOAD), sha: str = PAYLOAD_SHA256) -> dict:
    return {
        "backend": "github",
        "locator": "owner/repo@tag/asset.bin",
        "size": size,
        "sha256": sha,
        "extra": {"asset_names": ["asset.bin"]},
    }


@pytest.fixture()
def patch_resolve(monkeypatch, fake_server):
    """Point the prover's asset resolution at the fake server."""
    port = fake_server.server_address[1]

    def _fake(repo, tag, names, token):
        assert repo == "owner/repo" and tag == "tag"
        return [
            _AssetInfo(name=n, api_url=f"http://127.0.0.1:{port}/asset/{n}", size=len(PAYLOAD))
            for n in names
        ]

    monkeypatch.setattr(retrievability, "_resolve_asset_infos", _fake)
    monkeypatch.setattr(retrievability, "_gh_auth_token", lambda: None)
    # No sleeping in unit tests.
    monkeypatch.setattr(retrievability, "_backoff", lambda attempt: None)
    return fake_server


def _prove(ref, tmp_path, **kw):
    return prove_retrievable(ref, scratch_dir=str(tmp_path / "scratch"), **kw)


# ---------------------------------------------------------------- happy path
def test_prove_happy_path(patch_resolve, tmp_path):
    result = _prove(_ref("x"), tmp_path)
    assert isinstance(result, ProofResult)
    assert result.proven is True
    assert result.hash_matched is True
    assert result.size_matched is True
    assert result.bytes_fetched == len(PAYLOAD)
    assert result.actual_sha256 == PAYLOAD_SHA256
    assert result.attempts == 1
    assert result.reason
    # Scratch is cleaned up.
    assert list((tmp_path / "scratch").iterdir()) == []


def test_prove_multi_asset_concatenated_hash(patch_resolve, tmp_path, monkeypatch, fake_server):
    """Split-asset uploads hash the concatenated parts in sorted order."""
    port = fake_server.server_address[1]
    half = len(PAYLOAD) // 2
    a, b = PAYLOAD[:half], PAYLOAD[half:]
    both = a + b
    both_sha = hashlib.sha256(both).hexdigest()

    def _fake(repo, tag, names, token):
        assert names == ["p-aa", "p-ab"]  # sorted by the prover
        return [
            _AssetInfo(name="p-aa", api_url=f"http://127.0.0.1:{port}/a", size=half),
            _AssetInfo(name="p-ab", api_url=f"http://127.0.0.1:{port}/b", size=len(PAYLOAD) - half),
        ]

    monkeypatch.setattr(retrievability, "_resolve_asset_infos", _fake)

    real_download = retrievability._download_one

    def _sliced(info, dest, token, stats, resolve_fresh_url=None):
        # Serve each asset its slice of the payload.
        _ScriptedHandler.payload = a if info.name == "p-aa" else b
        try:
            real_download(info, dest, token, stats, resolve_fresh_url)
        finally:
            _ScriptedHandler.payload = PAYLOAD

    monkeypatch.setattr(retrievability, "_download_one", _sliced)
    ref = _ref("x")
    ref["size"] = len(both)
    ref["sha256"] = both_sha
    ref["extra"] = {"asset_names": ["p-ab", "p-aa"]}  # unsorted input
    result = _prove(ref, tmp_path)
    assert result.proven is True, result.reason
    assert result.bytes_fetched == len(both)


# ---------------------------------------------------------------- fail closed
def test_hash_mismatch_refuses(patch_resolve, tmp_path):
    result = _prove(_ref("x", sha="0" * 64), tmp_path)
    assert result.proven is False
    assert result.hash_matched is False
    assert result.size_matched is True
    assert result.actual_sha256 == PAYLOAD_SHA256
    assert "mismatch" in result.reason


def test_missing_sha256_refuses(tmp_path):
    result = _prove(_ref("x", sha=""), tmp_path)
    assert result.proven is False
    assert "sha256" in result.reason


def test_unsupported_backend_refuses(tmp_path):
    ref = _ref("x")
    ref["backend"] = "terabox"
    result = _prove(ref, tmp_path)
    assert result.proven is False
    assert "terabox" in result.reason
    assert "fail closed" in result.reason


def test_bad_locator_refuses(tmp_path):
    ref = _ref("x")
    ref["locator"] = "not-a-locator"
    result = _prove(ref, tmp_path)
    assert result.proven is False
    assert "locator" in result.reason


def test_asset_not_found_refuses(patch_resolve, tmp_path, monkeypatch):
    def _missing(repo, tag, names, token):
        raise LookupError("asset 'nope' not found on release owner/repo@tag")

    monkeypatch.setattr(retrievability, "_resolve_asset_infos", _missing)
    ref = _ref("x")
    ref["locator"] = "owner/repo@tag/nope"
    result = _prove(ref, tmp_path)
    assert result.proven is False
    assert "not found" in result.reason


def test_size_mismatch_refuses_fail_fast(patch_resolve, tmp_path):
    # The release API says 100 KiB but the ref claims 200 KiB: the store
    # changed under us — refuse before fetching a single byte.
    result = _prove(_ref("x", size=2 * len(PAYLOAD)), tmp_path)
    assert result.proven is False
    assert result.attempts == 0
    assert result.bytes_fetched == 0
    assert "release metadata size" in result.reason


# ---------------------------------------------------------------- retries
def test_retry_on_500_then_succeeds(patch_resolve, tmp_path):
    _ScriptedHandler.failures = [500, 500]
    result = _prove(_ref("x"), tmp_path)
    assert result.proven is True, result.reason
    assert result.attempts == 3


def test_retry_on_transient_401_then_succeeds(patch_resolve, tmp_path):
    _ScriptedHandler.failures = [401]
    result = _prove(_ref("x"), tmp_path)
    assert result.proven is True, result.reason
    assert result.attempts == 2


def test_empty_response_is_retried(patch_resolve, tmp_path):
    _ScriptedHandler.failures = ["empty"]
    result = _prove(_ref("x"), tmp_path)
    assert result.proven is True, result.reason
    assert result.attempts == 2


def test_midstream_drop_resumes_via_range(patch_resolve, tmp_path):
    _ScriptedHandler.failures = ["drop"]
    result = _prove(_ref("x"), tmp_path)
    assert result.proven is True, result.reason
    assert result.bytes_fetched == len(PAYLOAD)
    assert result.actual_sha256 == PAYLOAD_SHA256
    assert result.attempts == 2  # first dropped, second resumed


def test_permanent_404_refuses_without_retry(patch_resolve, tmp_path):
    _ScriptedHandler.failures = [404]
    result = _prove(_ref("x"), tmp_path)
    assert result.proven is False
    assert result.attempts == 1
    assert "404" in result.reason


# ---------------------------------------------------------------- registry glue
def test_prove_registry_entry_builds_locator(patch_resolve, tmp_path, monkeypatch):
    seen = {}

    def _fake(repo, tag, names, token):
        seen.update(repo=repo, tag=tag, names=names)
        port = patch_resolve.server_address[1]
        return [
            _AssetInfo(name=n, api_url=f"http://127.0.0.1:{port}/a/{n}", size=len(PAYLOAD))
            for n in names
        ]

    monkeypatch.setattr(retrievability, "_resolve_asset_infos", _fake)
    entry = {
        "store": "github",
        "repo": "owner/repo",
        "release": "tag",
        "asset": "asset.bin",
        "sha256": PAYLOAD_SHA256,
        "size": len(PAYLOAD),
    }
    result = prove_registry_entry(entry, scratch_dir=str(tmp_path / "scratch"))
    assert result.proven is True, result.reason
    assert result.locator == "owner/repo@tag/asset.bin"
    assert seen == {"repo": "owner/repo", "tag": "tag", "names": ["asset.bin"]}


def test_prove_registry_entry_terabox_refuses(tmp_path):
    entry = {"store": "terabox", "remote_dir": "/x", "sha256": "a" * 64, "size": 10}
    result = prove_registry_entry(entry, scratch_dir=str(tmp_path / "scratch"))
    assert result.proven is False
    assert "fail closed" in result.reason


def test_prove_registry_entry_missing_fields_refuses(tmp_path):
    result = prove_registry_entry({"store": "github"}, scratch_dir=str(tmp_path))
    assert result.proven is False
    assert result.reason


# ---------------------------------------------------------------- live test
# Oracle for the expected digest: independent path — `gh release download`
# + `sha256sum` (NOT the prover's download code). Verified 2026-10-20.
LIVE_SHA256 = "9346ecf37bc0240026c08d5019ef14cfbb02fd378bf3a890548b15692e255298"
LIVE_SIZE = 53122
LIVE_REPO = "mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant"
LIVE_TAG = "engine-v1"
LIVE_ASSET = "clouddog-engine-v1.tar.gz"


@pytest.mark.skipif(
    os.environ.get("CLOUDDOG_LIVE_TEST") != "1" or shutil.which("gh") is None,
    reason="live GitHub pull-back test; set CLOUDDOG_LIVE_TEST=1 to run",
)
def test_live_prove_real_release_asset(tmp_path):
    """Pull a REAL 53KB release asset and hash-verify it end to end."""
    ref = {
        "backend": "github",
        "locator": f"{LIVE_REPO}@{LIVE_TAG}/{LIVE_ASSET}",
        "size": LIVE_SIZE,
        "sha256": LIVE_SHA256,
        "extra": {"asset_names": [LIVE_ASSET]},
    }
    result = prove_retrievable(ref, scratch_dir=str(tmp_path / "scratch"))
    assert result.proven is True, result.reason
    assert result.bytes_fetched == LIVE_SIZE
    assert result.hash_matched is True
    assert result.actual_sha256 == LIVE_SHA256
