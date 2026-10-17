"""Tests for the CloudDog dashboard backend.

Run: PYTHONPATH=src python -m pytest tests/test_dashboard.py -q

The dashboard is tested standalone: engine modules (policy, stores.*,
reaper, audit, daemon, torrent, cli) are built by parallel lanes, so any
endpoint that needs them must return the honest "not available" error
instead of crashing.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

import pytest

from clouddog.dashboard.server import DashboardHandler


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()


def get(base_url, path):
    req = urllib.request.Request(base_url + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def post(base_url, path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        base_url + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_health(base_url):
    status, body = get(base_url, "/api/health")
    assert status == 200
    assert body["ok"] is True
    assert body["version"] == "0.1.0"
    assert "time_utc" in body


def test_disk_matches_shutil(base_url):
    status, body = get(base_url, "/api/disk")
    assert status == 200
    # Same resolution as the server: CLOUDEDOG_DISK_PATH or /home/hatch,
    # falling back to / (e.g. CI runners).
    from clouddog.dashboard.server import _data_root
    root = _data_root()
    usage = shutil.disk_usage(root)
    assert body["path"] == root
    assert body["total_bytes"] == usage.total
    # used/free can move between the server's stat and ours on a live
    # system — compare approximately.
    assert body["used_bytes"] == pytest.approx(usage.used, rel=0.01)
    assert body["free_bytes"] == pytest.approx(usage.free, rel=0.01)
    assert body["use_pct"] == pytest.approx(usage.used / usage.total * 100, abs=1.0)


def test_backends_shape(base_url):
    status, body = get(base_url, "/api/backends")
    assert status == 200
    assert isinstance(body, list) and body
    for b in body:
        assert set(b.keys()) >= {"name", "health", "reason"}
        assert b["health"] in ("live", "degraded", "down")


def test_sysinfo_mem_matches_proc(base_url):
    status, body = get(base_url, "/api/sysinfo")
    assert status == 200
    with open("/proc/meminfo") as fh:
        memtotal = next(
            int(l.split()[1]) * 1024 for l in fh if l.startswith("MemTotal:")
        )
    assert body["mem"]["total_bytes"] == memtotal
    assert "load_avg_1min" in body["cpu"]  # labeled load average, never a fake %
    assert "cpu_pct" not in body["cpu"]
    assert body["gpu"] is None or "name" in body["gpu"]


def test_du_tmp(base_url):
    status, body = get(base_url, "/api/du?path=/tmp&depth=1")
    assert status == 200
    assert isinstance(body["entries"], list)


def test_cli_scan_tmp(base_url):
    status, body = post(base_url, "/api/cli", {"argv": ["scan", "--path", "/tmp"]})
    # Engine present: real scan, returncode 0. Engine absent/broken: the
    # honest "not available" error. Never a crash.
    if status == 503:
        assert body["ok"] is False
        assert "not available" in body["error"]
    else:
        assert status == 200, body
        assert body["ok"] is True
        assert body["returncode"] == 0
        assert isinstance(body["stdout"], str)


def test_cli_rejects_non_allowlisted(base_url):
    status, body = post(base_url, "/api/cli", {"argv": ["rm", "-rf", "/"]})
    assert status == 403
    assert body["ok"] is False


def test_du_traversal_rejected(base_url):
    status, body = get(base_url, "/api/du?path=/etc")
    assert 400 <= status < 500
    assert body["ok"] is False
    assert "entries" not in body


def test_du_symlink_escape_rejected(base_url, tmp_path):
    link = tmp_path / "escape"
    link.symlink_to("/etc")
    status, body = get(base_url, f"/api/du?path={link}")
    assert 400 <= status < 500
    assert body["ok"] is False


def test_torrent_status_shape(base_url):
    status, body = get(base_url, "/api/torrent/status")
    assert status == 200
    assert set(body.keys()) >= {"available", "reason", "version", "torrents"}
    assert isinstance(body["torrents"], list)
    if not body["available"]:
        assert body["reason"]  # honest reason, never a fake torrent list


def test_daemon_install_generates_real_configs(base_url):
    for os_name in ("linux", "macos", "windows"):
        status, body = post(
            base_url,
            "/api/daemon/install",
            {"os": os_name, "interval_minutes": 60},
        )
        assert status == 200, body
        assert body["ok"] is True
        assert body["files"]
        if os_name == "macos":
            # plist ProgramArguments are separate array elements by format
            for token in ("clouddog", "daemon", "--interval", "60"):
                assert any(token in f["content"] for f in body["files"]), token
        else:
            assert any(
                "clouddog daemon --interval 60" in f["content"] for f in body["files"]
            ), "no generated file runs the daemon command"
    status, body = post(
        base_url, "/api/daemon/install", {"os": "plan9", "interval_minutes": 60}
    )
    assert status == 400


def test_broadcast_draft_formats_only(base_url):
    status, body = post(
        base_url,
        "/api/broadcast/draft",
        {"title": "Test Law", "laws": [{"name": "NO-X", "text": "Never do X."}]},
    )
    assert status == 200
    assert body["ok"] is True
    assert "# Proposal: Test Law" in body["markdown"]
    assert "NO-X" in body["markdown"]
    assert "Never do X." in body["markdown"]


def test_lanes_honest_unknowns(base_url):
    status, body = get(base_url, "/api/lanes")
    assert status == 200
    assert isinstance(body, list)
    for lane in body:
        assert set(lane.keys()) >= {
            "lane", "manifest_path", "uploaded", "total",
            "pct", "alive", "log_tail",
        }
        assert isinstance(lane["log_tail"], list)


def test_static_index_served(base_url):
    req = urllib.request.Request(base_url + "/", method="GET")
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200
        html = resp.read().decode()
    assert "CloudDog Dashboard" in html
    assert "/static/app.js" in html


def test_relocate_dry_run_touches_nothing(base_url, tmp_path):
    # small dir under /tmp (allowed root) so the sha256 walk stays fast
    src = tmp_path / "reloc_src"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "sub").mkdir()
    (src / "sub" / "b.bin").write_bytes(b"\x00" * 1024)
    status, body = post(
        base_url,
        "/api/relocate",
        {"path": str(src), "tier": "github", "remote_ref": "o/r@t", "dry_run": True},
    )
    # Engine may be absent (503), backend not live (409), or a dry-run
    # preview (200). In every case: nothing stored, nothing deleted.
    assert status in (200, 409, 503), body
    if status == 200:
        assert body["ok"] is True
        assert body["dry_run"] is True
        assert isinstance(body["manifest"], list) and len(body["manifest"]) == 2
        assert body["manifest"][0]["sha256"]
    else:
        assert body["ok"] is False
        assert body["error"]
    assert src.is_dir()  # dry_run must never delete


def test_sweep_returns_job_immediately(base_url):
    # Sweep must not block: 202 + job id, pollable via /api/sweep/status.
    import urllib.request

    data = json.dumps({"dry_run": True}).encode()
    req = urllib.request.Request(
        base_url + "/api/sweep", data=data,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 202
        body = json.loads(r.read())
    assert body["ok"] is True
    assert body["status"] == "running"
    job_id = body["job_id"]

    status, st = get(base_url, f"/api/sweep/status?job={job_id}")
    assert status == 200
    assert st["job_id"] == job_id
    assert st["status"] in ("running", "done", "error")

    status, _ = get(base_url, "/api/sweep/status?job=nope")
    assert status == 404
