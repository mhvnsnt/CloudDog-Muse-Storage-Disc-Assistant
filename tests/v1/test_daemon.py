"""Tests for clouddog.daemon.

policy.scan is monkeypatched (the policy module itself is built in parallel,
so only its scan() surface is touched). Backends and the journal are faked
and injected through the Daemon constructor.
"""

from __future__ import annotations

import collections
import hashlib
import importlib
import os
import shutil
import sys
import types
from enum import Enum

import pytest

from clouddog.daemon import Daemon, TickReport
from clouddog.errors import PolicyViolation, StoreDegraded
from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef

# ------------------------------------------------- policy (real or stubbed)


def _policy_stub():
    mod = types.ModuleType("clouddog.policy")

    class Action(Enum):
        STORE = "store"
        STORE_AND_CLEAR = "store_and_clear"
        KEEP_LOCAL = "keep_local"
        SACRED = "sacred"
        NEVER_DELETE = "never_delete"

    def scan(root, max_entries=200):
        return []

    mod.Action = Action
    mod.scan = scan
    return mod


try:
    policy_mod = importlib.import_module("clouddog.policy")
except ImportError:
    policy_mod = _policy_stub()
    sys.modules["clouddog.policy"] = policy_mod
    import clouddog

    clouddog.policy = policy_mod


# ------------------------------------------------------------------ fakes


class FakeBackend(StoreBackend):
    """Controllable LIVE/DEGRADED backend recording every call."""

    name = "fake"

    def __init__(self, health=Health.LIVE, verify_result=True, store_exc=None, verify_exc=None):
        self._health = HealthStatus(health, "fake")
        self._verify_result = verify_result
        self._store_exc = store_exc
        self._verify_exc = verify_exc
        self.store_calls = []

    def health(self):
        return self._health

    def store(self, local_path, remote_ref, dry_run=False):
        self.store_calls.append({"local": local_path, "remote_ref": remote_ref, "dry_run": dry_run})
        if self._store_exc is not None:
            raise self._store_exc
        with open(local_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return StoredRef(
            backend="fake",
            locator=remote_ref,
            size=os.path.getsize(local_path),
            sha256=digest,
            extra={"dry_run": dry_run},
        )

    def verify(self, local_path, stored):
        if self._verify_exc is not None:
            raise self._verify_exc
        return self._verify_result


class FakeJournal:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def read(self, limit=100):
        return list(self.events[-limit:])

    def kinds(self):
        out = []
        for e in self.events:
            out.append(e.kind if hasattr(e, "kind") else e[1])
        return out


Usage = collections.namedtuple("usage", "total used free")


def _low_disk(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(1000, 100, 900))


def _high_disk(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100, 90, 10))


def _make_daemon(tmp_path, monkeypatch, entries, backend=None, **kwargs):
    backend = backend or FakeBackend()
    journal = FakeJournal()
    monkeypatch.setattr(policy_mod, "scan", lambda root, max_entries=200: list(entries))
    daemon = Daemon(
        roots=[str(tmp_path)],
        backends={"github": backend},
        journal=journal,
        disk_path=str(tmp_path),
        **kwargs,
    )
    return daemon, backend, journal


def _write(tmp_path, name, content=b"data" * 64):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


# ------------------------------------------------------------------- ticks


def test_tick_store_verify_clear_deletes_and_journals(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "big.bin")
    size = os.path.getsize(target)
    entries = [
        {
            "path": target,
            "bytes": size,
            "action": "store_and_clear",
            "tier": "github",
            "reason": "test",
        }
    ]
    daemon, backend, journal = _make_daemon(tmp_path, monkeypatch, entries)
    # M5: the daemon re-evaluates policy just before delete; make the
    # pre-delete check agree with the test entry.
    from clouddog.policy import Action, Decision

    monkeypatch.setattr(
        policy_mod,
        "evaluate",
        lambda p, scan_root=None: Decision(Action.STORE_AND_CLEAR, "github", "test"),
    )

    report = daemon.tick()

    assert isinstance(report, TickReport)
    assert report.stored == 1
    assert report.verified == 1
    assert report.cleared == 1
    assert report.freed_bytes == size
    assert report.freed_bytes > 0
    assert not os.path.exists(target)
    assert backend.store_calls[0]["dry_run"] is False
    assert backend.store_calls[0]["remote_ref"].startswith("mhvnsnt/clouddog-storage@clouddog-")
    kinds = journal.kinds()
    assert "stored" in kinds and "verified" in kinds and "cleared" in kinds
    assert report.critical is False


def test_tick_store_without_clear_keeps_file(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "keep.bin")
    entries = [
        {
            "path": target,
            "bytes": os.path.getsize(target),
            "action": "store",
            "tier": "github",
            "reason": "test",
        }
    ]
    daemon, _backend, _journal = _make_daemon(tmp_path, monkeypatch, entries)

    report = daemon.tick()

    assert report.stored == 1 and report.verified == 1
    assert report.cleared == 0
    assert os.path.exists(target)


def test_tick_dry_run_deletes_nothing(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "big.bin")
    entries = [
        {
            "path": target,
            "bytes": os.path.getsize(target),
            "action": "store_and_clear",
            "tier": "github",
            "reason": "test",
        }
    ]
    daemon, backend, journal = _make_daemon(tmp_path, monkeypatch, entries, dry_run=True)

    report = daemon.tick()

    assert report.stored == 1 and report.verified == 1
    assert report.cleared == 0 and report.freed_bytes == 0
    assert os.path.exists(target)
    assert backend.store_calls[0]["dry_run"] is True
    assert "cleared" not in journal.kinds()


def test_tick_degraded_backend_keeps_file_and_records_error(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "big.bin")
    entries = [
        {
            "path": target,
            "bytes": os.path.getsize(target),
            "action": "store_and_clear",
            "tier": "github",
            "reason": "test",
        }
    ]
    backend = FakeBackend(health=Health.DEGRADED)
    daemon, _, _journal = _make_daemon(tmp_path, monkeypatch, entries, backend=backend)

    report = daemon.tick()

    assert report.stored == 0 and report.cleared == 0
    assert os.path.exists(target)
    assert backend.store_calls == []
    assert len(report.errors) == 1
    assert "github" in report.errors[0]
    assert report.critical is False  # degraded on a small file, disk fine


def test_tick_never_deletes_when_verify_returns_false(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "big.bin")
    entries = [
        {
            "path": target,
            "bytes": os.path.getsize(target),
            "action": "store_and_clear",
            "tier": "github",
            "reason": "test",
        }
    ]
    backend = FakeBackend(verify_result=False)
    daemon, _, journal = _make_daemon(tmp_path, monkeypatch, entries, backend=backend)

    report = daemon.tick()

    assert report.stored == 1 and report.verified == 0
    assert report.cleared == 0
    assert os.path.exists(target)
    assert report.critical is True
    assert "verification" in report.critical_msg.lower()
    assert "cleared" not in journal.kinds()


def test_tick_sacred_never_delete_keep_local_are_untouched(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    paths = [_write(tmp_path, f"f{i}.bin") for i in range(3)]
    entries = [
        {"path": paths[0], "bytes": 1, "action": "sacred", "tier": "github", "reason": "t"},
        {"path": paths[1], "bytes": 1, "action": "never_delete", "tier": "github", "reason": "t"},
        {"path": paths[2], "bytes": 1, "action": "keep_local", "tier": "github", "reason": "t"},
    ]
    daemon, backend, journal = _make_daemon(tmp_path, monkeypatch, entries)

    report = daemon.tick()

    assert report.skipped_sacred == 3
    assert report.stored == 0 and report.cleared == 0
    assert backend.store_calls == []
    for p in paths:
        assert os.path.exists(p)
    assert "skipped" in journal.kinds()


def test_tick_missing_backend_records_error(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "big.bin")
    entries = [
        {
            "path": target,
            "bytes": os.path.getsize(target),
            "action": "store_and_clear",
            "tier": "terabox",
            "reason": "test",
        }
    ]
    daemon, _, _ = _make_daemon(tmp_path, monkeypatch, entries)

    report = daemon.tick()

    assert report.stored == 0 and report.cleared == 0
    assert os.path.exists(target)
    assert any("terabox" in e for e in report.errors)


def test_tick_critical_when_disk_above_85(tmp_path, monkeypatch):
    _high_disk(monkeypatch)
    daemon, _, _ = _make_daemon(tmp_path, monkeypatch, [])

    report = daemon.tick()

    assert report.disk_after_pct == pytest.approx(90.0)
    assert report.critical is True
    assert "85" in report.critical_msg


def test_tick_critical_on_backend_error_for_big_file(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = _write(tmp_path, "huge.bin")
    entries = [
        {
            "path": target,
            "bytes": 2 * 1024**3,  # >1GB per policy bytes
            "action": "store_and_clear",
            "tier": "github",
            "reason": "test",
        }
    ]
    backend = FakeBackend(store_exc=StoreDegraded("fake", "down"))
    daemon, _, _ = _make_daemon(tmp_path, monkeypatch, entries, backend=backend)

    report = daemon.tick()

    assert report.stored == 0 and report.cleared == 0
    assert os.path.exists(target)
    assert report.critical is True
    assert ">1GB" in report.critical_msg


def test_delete_local_raises_policy_violation_for_protected(tmp_path):
    daemon = Daemon(
        roots=[str(tmp_path)], backends={}, journal=FakeJournal(), disk_path=str(tmp_path)
    )
    target = _write(tmp_path, "sacred.bin")

    for action in ("sacred", "never_delete", "keep_local", "store"):
        with pytest.raises(PolicyViolation):
            daemon._delete_local(target, action)
    assert os.path.exists(target)


def test_tick_report_field_order_and_defaults():
    r = TickReport(
        started_utc="t",
        disk_before_pct=1.0,
        disk_after_pct=2.0,
        stored=0,
        verified=0,
        cleared=0,
        freed_bytes=0,
        skipped_sacred=0,
        errors=[],
        critical=False,
    )
    assert r.critical_msg == ""
    assert r.errors == []
