"""Tests for the consolidated stream mode: CLI + daemon config-file hook.

Covers the wiring added on top of clouddog.streamer: the `stream`
subcommand (health gate, TeraBox refusal, deletion logging), the
daemon's stream-dirs.json pass (content-verify refusal, counters,
heartbeat), and the deletion-log rule (repo AGENTS.md rule 7).
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from clouddog import cli
from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef

MB = 1024 * 1024


class FakeBackend(StoreBackend):
    """In-memory backend implementing the v1 StoreBackend contract."""

    name = "fake"

    def __init__(self, health=Health.LIVE):
        self.blobs: dict[str, bytes] = {}
        self._health = health

    def health(self) -> HealthStatus:
        return HealthStatus(self._health, "fake")

    def store(self, local_path, remote_ref, dry_run=False) -> StoredRef:
        with open(local_path, "rb") as fh:
            data = fh.read()
        digest = hashlib.sha256(data).hexdigest()
        base = os.path.basename(local_path)
        if dry_run:
            return StoredRef(self.name, f"dry-run:{remote_ref}/{base}", len(data), digest)
        self.blobs[base] = data
        return StoredRef(self.name, f"{remote_ref}/{base}", len(data), digest)

    def verify(self, local_path, stored) -> bool:
        try:
            with open(local_path, "rb") as fh:
                data = fh.read()
        except OSError:
            return False
        return hashlib.sha256(data).hexdigest() == stored.sha256


class NoVerifyBackend(FakeBackend):
    supports_content_verify = False


def _drop(wd: str, name: str, size: int, mtime: float = 5000.0) -> str:
    p = os.path.join(wd, name)
    with open(p, "wb") as fh:
        fh.write(os.urandom(size))
    os.utime(p, (mtime, mtime))
    return p


class _Journal:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def read(self, limit=100):
        return self.events[-limit:]


# ---------------------------------------------------------------- parse


def test_parse_bytes_ok():
    assert cli._parse_bytes("104857600") == 104857600
    assert cli._parse_bytes("100M") == 100 * MB
    assert cli._parse_bytes("100MB") == 100 * MB
    assert cli._parse_bytes("2G") == 2 * 1024 * MB
    assert cli._parse_bytes(7) == 7


def test_parse_bytes_bad():
    with pytest.raises(ValueError):
        cli._parse_bytes("lots")


# ---------------------------------------------------------------- CLI


def _argv(wd, journal, *extra):
    return [
        "stream",
        "--watch-dir",
        wd,
        "--settle-secs",
        "0",
        "--journal",
        str(journal),
        "--repo",
        "o/r",
        "--tag",
        "t",
        "--chunk-bytes",
        "1M",
        *extra,
    ]


def test_cli_stream_apply_clears_and_logs(tmp_path, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(cli, "_load_backend", lambda tier: backend)
    md_log = tmp_path / "disk-cleanup-log.md"
    monkeypatch.setattr(cli, "DISK_CLEANUP_LOG", str(md_log))
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "apply.bin", 2 * MB)
    argv = _argv(str(wd), tmp_path / "j.jsonl", "--apply")

    assert cli.main(argv) == 0  # first pass: settling
    assert cli.main(argv) == 0  # second pass: streams + clears
    assert not os.path.exists(p)
    assert any(k.startswith("apply.bin") for k in backend.blobs)
    # Audit journal saw the clear; disk-cleanup-log.md names the backup.
    events = (tmp_path / "j.jsonl").read_text()
    assert '"cleared"' in events
    md = md_log.read_text()
    assert "CLEARED" in md and "apply.bin" in md and "o/r@t" in md


def test_cli_stream_terabox_refused_for_apply(tmp_path):
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "tb.bin", 2 * MB)
    argv = _argv(str(wd), tmp_path / "j.jsonl", "--apply", "--tier", "terabox")
    assert cli.main(argv) == 3
    assert os.path.exists(p)  # fail closed: nothing touched


def test_cli_stream_refuses_down_backend(tmp_path, monkeypatch):
    backend = FakeBackend(health=Health.DOWN)
    monkeypatch.setattr(cli, "_load_backend", lambda tier: backend)
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "down.bin", 2 * MB)
    argv = _argv(str(wd), tmp_path / "j.jsonl", "--apply")
    assert cli.main(argv) == 3
    assert os.path.exists(p)
    assert backend.blobs == {}


# ---------------------------------------------------------------- daemon


def _daemon(tmp_path, backend, cfg_entries, dry_run=True):
    from clouddog.daemon import Daemon

    journal = _Journal()
    d = Daemon(
        roots=[str(tmp_path / "roots")],
        backends={},
        journal=journal,
        disk_path=str(tmp_path),
        dry_run=dry_run,
        stream_backend=backend,
    )
    (tmp_path / "roots").mkdir(exist_ok=True)
    cfg = tmp_path / "stream-dirs.json"
    cfg.write_text(json.dumps(cfg_entries))
    d.stream_dirs_path = str(cfg)
    d.heartbeat_path = str(tmp_path / "heartbeat.json")
    return d, journal


def test_daemon_stream_counters_and_heartbeat(tmp_path, monkeypatch):
    md_log = tmp_path / "disk-cleanup-log.md"
    monkeypatch.setattr(cli, "DISK_CLEANUP_LOG", str(md_log))
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "landed.bin", 3 * MB)
    backend = FakeBackend()
    d, _journal = _daemon(
        tmp_path,
        backend,
        [{"watch_dir": str(wd), "repo": "o/r", "tag": "t", "chunk_mb": 1, "settle_secs": 0}],
        dry_run=False,
    )
    d.tick()  # first sighting: settling
    rep = d.tick()  # settled: streams + clears inside the tick
    assert rep.stream_files == 1
    assert rep.stream_chunks >= 3
    assert rep.stream_freed_bytes == 3 * MB
    assert rep.cleared >= 1 and rep.freed_bytes >= 3 * MB
    assert not os.path.exists(p)
    assert "landed.bin" in backend.blobs or any(k.startswith("landed.bin") for k in backend.blobs)
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["stream_files"] == 1
    assert hb["stream_freed_bytes"] == 3 * MB
    assert "CLEARED" in md_log.read_text()


def test_daemon_stream_refuses_backend_without_content_verify(tmp_path):
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "game.bin", 3 * MB)
    d, journal = _daemon(
        tmp_path,
        NoVerifyBackend(),
        [{"watch_dir": str(wd), "repo": "o/r", "tag": "t", "chunk_mb": 1, "settle_secs": 0}],
        dry_run=False,
    )
    d.tick()
    d.tick()
    assert os.path.exists(p)  # fail closed: no byte-proof, no clear
    kinds = {getattr(e, "kind", "") for e in journal.events}
    assert "error" in kinds


def test_daemon_stream_skips_when_backend_not_live(tmp_path):
    wd = tmp_path / "watch"
    wd.mkdir()
    p = _drop(str(wd), "x.bin", 3 * MB)
    d, _journal = _daemon(
        tmp_path,
        FakeBackend(health=Health.DOWN),
        [{"watch_dir": str(wd), "repo": "o/r", "tag": "t", "chunk_mb": 1, "settle_secs": 0}],
        dry_run=False,
    )
    rep = d.tick()
    assert rep.stream_files == 0
    assert os.path.exists(p)
