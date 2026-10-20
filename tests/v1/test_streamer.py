"""Tests for clouddog.streamer (Patch 1: manifest + settle scan + planning).

Pure functions, no network, no store backends.
"""

from __future__ import annotations

import os

import pytest

from clouddog import streamer
from clouddog.errors import CloudDogError

MB = 1024 * 1024


# ------------------------------------------------------------- chunk_plan


def test_chunk_plan_exact_multiple():
    assert streamer.chunk_plan(200 * MB, 100 * MB) == [
        (0, 0, 100 * MB),
        (1, 100 * MB, 100 * MB),
    ]


def test_chunk_plan_remainder():
    assert streamer.chunk_plan(250 * MB, 100 * MB) == [
        (0, 0, 100 * MB),
        (1, 100 * MB, 100 * MB),
        (2, 200 * MB, 50 * MB),
    ]


def test_chunk_plan_single_chunk():
    assert streamer.chunk_plan(50 * MB, 100 * MB) == [(0, 0, 50 * MB)]


def test_chunk_plan_empty():
    assert streamer.chunk_plan(0, 100 * MB) == []


def test_chunk_plan_bad_chunk_size():
    with pytest.raises(ValueError):
        streamer.chunk_plan(100, 0)


# ------------------------------------------------------------- asset names


def test_chunk_asset_name_single():
    assert streamer.chunk_asset_name("file.bin", 0, 1) == "file.bin"


def test_chunk_asset_name_multi():
    assert streamer.chunk_asset_name("file.bin", 2, 5) == "file.bin.chunk-0002"
    assert streamer.chunk_asset_name("file.bin", 0, 5) == "file.bin.chunk-0000"


def test_manifest_asset_name():
    assert streamer.manifest_asset_name("file.bin") == "file.bin.stream-manifest.json"


# ------------------------------------------------------------- manifest


def test_manifest_roundtrip(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag-1", chunk_bytes=10 * MB)
    rec = streamer.FileRecord(
        name="a.bin",
        path=os.path.join(wd, "a.bin"),
        size=12,
        sha256="abc",
        strict=True,
        status="streaming",
        chunks=[
            streamer.ChunkRecord(
                index=0, asset="a.bin", size=12, sha256="abc", locator="loc", verified=True
            )
        ],
    )
    m.files["a.bin"] = rec
    m.seen["b.bin"] = [5, 1.5, 2.5]
    streamer.save_manifest(m)

    m2 = streamer.load_manifest(wd, "o/r", "tag-1", chunk_bytes=10 * MB)
    r2 = m2.files["a.bin"]
    assert r2.size == 12 and r2.strict and r2.status == "streaming"
    assert r2.chunks[0].asset == "a.bin" and r2.chunks[0].verified
    assert m2.seen["b.bin"] == [5, 1.5, 2.5]
    # Atomic write: no .tmp left behind.
    assert not os.path.exists(streamer.manifest_path(wd) + ".tmp")


def test_manifest_target_mismatch_refused(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag-1")
    streamer.save_manifest(m)
    with pytest.raises(CloudDogError):
        streamer.load_manifest(wd, "o/other", "tag-1")


# ------------------------------------------------------------- scan_watch


def _write(path: str, size: int, mtime: float) -> None:
    with open(path, "wb") as fh:
        fh.write(os.urandom(size))
    os.utime(path, (mtime, mtime))


def test_scan_settle_needs_two_unchanged_observations(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag")
    _write(os.path.join(wd, "f.bin"), 2 * MB, 1000.0)

    ready, skipped = streamer.scan_watch(wd, m, settle_secs=300, now=2000.0)
    assert ready == []  # first sighting: settling
    assert any(s[0] == "f.bin" for s in skipped)

    # Same size/mtime 400s later: ready.
    ready, _ = streamer.scan_watch(wd, m, settle_secs=300, now=2400.0)
    assert ready == [os.path.join(wd, "f.bin")]


def test_scan_size_change_resets_settle(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag")
    p = os.path.join(wd, "f.bin")
    _write(p, 2 * MB, 1000.0)
    streamer.scan_watch(wd, m, settle_secs=300, now=2000.0)
    _write(p, 3 * MB, 2100.0)  # writer appended more
    ready, _ = streamer.scan_watch(wd, m, settle_secs=300, now=2500.0)
    assert ready == []  # clock restarted


def test_scan_skips_inflight_suffix_and_empty_and_dirs(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag")
    _write(os.path.join(wd, "a.bin.part"), 2 * MB, 1000.0)
    _write(os.path.join(wd, "b.bin.aria2"), 2 * MB, 1000.0)
    open(os.path.join(wd, "empty.bin"), "wb").close()
    os.utime(os.path.join(wd, "empty.bin"), (1000.0, 1000.0))
    os.mkdir(os.path.join(wd, "subdir"))
    _write(os.path.join(wd, "ok.bin"), 2 * MB, 1000.0)
    streamer.scan_watch(wd, m, settle_secs=300, now=2000.0)
    ready, skipped = streamer.scan_watch(wd, m, settle_secs=300, now=2400.0)
    names_ready = {os.path.basename(p) for p in ready}
    assert "ok.bin" in names_ready
    assert "a.bin.part" not in names_ready and "b.bin.aria2" not in names_ready
    skip_names = {s[0] for s in skipped}
    assert "empty.bin" in skip_names and "subdir" in skip_names
    # The checkpoint manifest itself is invisible to the scan.
    assert streamer.MANIFEST_NAME not in skip_names


def test_scan_skips_do_not_delete(tmp_path):
    wd = str(tmp_path)
    m = streamer.load_manifest(wd, "o/r", "tag")
    with open(os.path.join(wd, ".DO_NOT_DELETE"), "w") as fh:
        fh.write("x")
    _write(os.path.join(wd, "v.bin"), 2 * MB, 1000.0)
    streamer.scan_watch(wd, m, settle_secs=300, now=2000.0)
    ready, _ = streamer.scan_watch(wd, m, settle_secs=300, now=2400.0)
    assert ready == []


def test_scan_skips_symlink(tmp_path):
    wd = str(tmp_path / "watch")
    os.mkdir(wd)
    m = streamer.load_manifest(wd, "o/r", "tag")
    real = os.path.join(str(tmp_path), "real.bin")
    _write(real, 2 * MB, 1000.0)
    os.symlink(real, os.path.join(wd, "link.bin"))
    streamer.scan_watch(wd, m, settle_secs=300, now=2000.0)
    ready, skipped = streamer.scan_watch(wd, m, settle_secs=300, now=2400.0)
    assert ready == []
    assert any(s[0] == "link.bin" for s in skipped)


def test_stream_gate_flags_game_files(tmp_path):
    wd = str(tmp_path)
    iso = os.path.join(wd, "game.iso")
    _write(iso, 2 * MB, 1000.0)
    allowed, strict, reason = streamer.stream_gate(iso, wd)
    assert allowed and strict and "strict" in reason.lower()

    plain = os.path.join(wd, "data.bin")
    _write(plain, 2 * MB, 1000.0)
    allowed, strict, _ = streamer.stream_gate(plain, wd)
    assert allowed and not strict

    # Fresh file is sacred regardless of the watch contract.
    fresh = os.path.join(wd, "fresh.bin")
    _write(fresh, 2 * MB, 1000.0)
    import time as _t

    os.utime(fresh, (_t.time(), _t.time()))
    allowed, _, _ = streamer.stream_gate(fresh, wd)
    assert not allowed


# ------------------------------------------------- Patch 2: streaming engine

import hashlib
import json
import types

from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef


class FakeBackend(StoreBackend):
    """In-memory v1 backend: store() reads bytes, keeps them by asset name."""

    name = "fake"

    def __init__(self, fail_on=None):
        self.blobs: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.fail_on = fail_on

    def health(self):
        return HealthStatus(Health.LIVE)

    def store(self, local_path, remote_ref, dry_run=False):
        with open(local_path, "rb") as fh:
            data = fh.read()
        name = os.path.basename(local_path)
        self.calls.append(name)
        if self.fail_on is not None and self.fail_on(name):
            raise CloudDogError(f"injected failure on {name}")
        digest = hashlib.sha256(data).hexdigest()
        if dry_run:
            return StoredRef(
                backend="fake",
                locator=f"dry-run:{remote_ref}/{name}",
                size=len(data),
                sha256=digest,
            )
        self.blobs[name] = data
        return StoredRef(
            backend="fake",
            locator=f"{remote_ref}/{name}",
            size=len(data),
            sha256=digest,
        )

    def verify(self, local_path, stored):
        with open(local_path, "rb") as fh:
            data = fh.read()
        return hashlib.sha256(data).hexdigest() == stored.sha256


def _settled_streamer(tmp_path, backend, apply=True, **kw):
    wd = str(tmp_path / "watch")
    os.mkdir(wd)
    return wd, streamer.Streamer(wd, backend, repo="o/r", tag="tag", apply=apply, **kw)


def _drop(wd, name, size, now, payload=None):
    p = os.path.join(wd, name)
    data = payload if payload is not None else os.urandom(size)
    with open(p, "wb") as fh:
        fh.write(data)
    os.utime(p, (now - 1000, now - 1000))
    return p, data


def test_stream_single_chunk_apply(tmp_path):
    backend = FakeBackend()
    wd, s = _settled_streamer(tmp_path, backend, chunk_bytes=10 * MB)
    now = 5000.0
    p, data = _drop(wd, "one.bin", 2 * MB, now)
    s.stream_once(now=now)  # first sighting: settling only
    rep = s.stream_once(now=now + 301)  # settle window elapsed: streams
    assert rep.files_cleared == 1 and rep.freed_bytes == 2 * MB
    assert not os.path.exists(p)
    assert backend.blobs["one.bin"] == data
    m = streamer.load_manifest(wd, "o/r", "tag", chunk_bytes=10 * MB)
    assert m.files["one.bin"].status == "cleared"
    # Final manifest asset was published from the checkpoint record.
    payload = json.loads(backend.blobs["one.bin.stream-manifest.json"])
    assert payload["sha256"] == hashlib.sha256(data).hexdigest()
    assert payload["chunks"][0]["asset"] == "one.bin"


def test_stream_multi_chunk(tmp_path):
    backend = FakeBackend()
    wd, s = _settled_streamer(tmp_path, backend, chunk_bytes=100 * 1024)
    now = 5000.0
    p, data = _drop(wd, "big.bin", 250 * 1024, now)
    s.stream_once(now=now)
    rep = s.stream_once(now=now + 301)
    assert rep.chunks_uploaded == 3
    assert not os.path.exists(p)
    joined = b"".join(backend.blobs[f"big.bin.chunk-{i:04d}"] for i in range(3))
    assert joined == data  # reassembly from stored chunks is byte-identical


def test_stream_dry_run_deletes_nothing(tmp_path):
    backend = FakeBackend()
    wd, s = _settled_streamer(tmp_path, backend, apply=False, chunk_bytes=10 * MB)
    now = 5000.0
    p, _ = _drop(wd, "dry.bin", 2 * MB, now)
    s.stream_once(now=now)
    rep = s.stream_once(now=now + 301)
    assert rep.files_streamed == 1 and rep.files_cleared == 0
    assert os.path.exists(p)
    assert backend.blobs == {}  # dry-run uploaded nothing real
    m = streamer.load_manifest(wd, "o/r", "tag", chunk_bytes=10 * MB)
    assert "dry.bin" not in m.files  # dry-run never persists file records


def test_stream_failure_keeps_source_and_resumes(tmp_path):
    state = {"fail": True}
    backend = FakeBackend(fail_on=lambda n: state["fail"] and n.endswith("chunk-0001"))
    wd, s = _settled_streamer(tmp_path, backend, chunk_bytes=100 * 1024)
    now = 5000.0
    p, _data = _drop(wd, "big.bin", 250 * 1024, now)
    s.stream_once(now=now)
    rep = s.stream_once(now=now + 301)
    assert rep.errors and os.path.exists(p)  # failed chunk: source kept
    m = streamer.load_manifest(wd, "o/r", "tag", chunk_bytes=100 * 1024)
    assert m.files["big.bin"].chunks[0].verified  # chunk 0 checkpointed

    state["fail"] = False
    backend.calls.clear()
    rep = s.stream_once(now=now + 301)
    assert rep.files_cleared == 1 and not os.path.exists(p)
    assert "big.bin.chunk-0000" not in backend.calls  # resumed, not redone


def test_build_final_manifest_from_checkpoint_only(tmp_path):
    # SCOOBY regression: the watch dir is already pruned; the published
    # manifest must come from the checkpoint record alone.
    rec = streamer.FileRecord(
        name="gone.bin",
        path="/nowhere/gone.bin",
        size=10,
        sha256="deadbeef",
        chunks=[
            streamer.ChunkRecord(
                index=1,
                asset="gone.bin.chunk-0001",
                size=4,
                sha256="b",
                locator="o/r@tag/gone.bin.chunk-0001",
                verified=True,
            ),
            streamer.ChunkRecord(
                index=0,
                asset="gone.bin.chunk-0000",
                size=6,
                sha256="a",
                locator="o/r@tag/gone.bin.chunk-0000",
                verified=True,
            ),
        ],
    )
    payload = streamer.build_final_manifest(rec, "o/r", "tag")
    assert [c["index"] for c in payload["chunks"]] == [0, 1]  # sorted
    assert payload["sha256"] == "deadbeef"


def test_stream_game_file_blocked_without_strict_flags(tmp_path):
    backend = FakeBackend()
    wd, s = _settled_streamer(tmp_path, backend, chunk_bytes=10 * MB)
    now = 5000.0
    p, data = _drop(wd, "game.iso", 2 * MB, now)
    s.stream_once(now=now)
    rep = s.stream_once(now=now + 301)
    # Streamed + verified on the store, but the local copy STAYS.
    assert backend.blobs["game.iso"] == data
    assert rep.files_blocked == 1 and os.path.exists(p)


def test_stream_game_file_clears_at_strict_bar(tmp_path):
    backend = FakeBackend()
    prover = lambda ref: types.SimpleNamespace(proven=True, reason="ok")
    wd, s = _settled_streamer(
        tmp_path,
        backend,
        chunk_bytes=10 * MB,
        retrievability_proven=True,
        owner_surfaced=True,
        prover=prover,
    )
    now = 5000.0
    p, _ = _drop(wd, "game.iso", 2 * MB, now)
    s.stream_once(now=now)
    rep = s.stream_once(now=now + 301)
    assert rep.files_cleared == 1 and not os.path.exists(p)
