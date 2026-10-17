"""Tests for clouddog.cli.

Sibling modules (policy/audit/reaper/store backends) are built in parallel:
each is imported for real when available, otherwise a minimal behavioral stub
is installed into sys.modules. Tests then monkeypatch the pieces they need.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import types
from dataclasses import dataclass, field
from enum import Enum

import pytest

from clouddog.cli import main
from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef


# ------------------------------------------------------- sibling resolution

def _sibling(name: str, builder):
    """Import a sibling module for real; install a stub if it is missing."""
    try:
        return importlib.import_module(name)
    except ImportError:
        mod = builder()
        sys.modules[name] = mod
        parent_name, _, attr = name.rpartition(".")
        parent = sys.modules.get(parent_name) or importlib.import_module(parent_name)
        setattr(parent, attr, mod)
        return mod


def _policy_stub():
    mod = types.ModuleType("clouddog.policy")

    class Action(Enum):
        STORE = "store"
        STORE_AND_CLEAR = "store_and_clear"
        KEEP_LOCAL = "keep_local"
        SACRED = "sacred"
        NEVER_DELETE = "never_delete"

    @dataclass
    class Decision:
        action: Action
        tier: str | None
        reason: str

    def evaluate(path):
        return Decision(Action.KEEP_LOCAL, "github", "stub")

    def scan(root, max_entries=200):
        return []

    mod.Action = Action
    mod.Decision = Decision
    mod.evaluate = evaluate
    mod.scan = scan
    return mod


def _audit_stub():
    mod = types.ModuleType("clouddog.audit")

    @dataclass
    class AuditEvent:
        ts: str = ""
        kind: str = "sweep"
        path: str = ""
        size: int | None = None
        locator: str | None = None
        note: str = ""

        def to_dict(self):
            return {"ts": self.ts, "kind": self.kind, "path": self.path,
                    "size": self.size, "locator": self.locator, "note": self.note}

    class AuditJournal:
        def __init__(self, jsonl_path, md_path=None):
            self._path = jsonl_path
            self._events = []
            os.makedirs(os.path.dirname(os.path.abspath(jsonl_path)), exist_ok=True)

        def record(self, event):
            self._events.append(event)
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict()) + "\n")

        def read(self, limit=100):
            return [e.to_dict() for e in self._events[-limit:]]

    mod.AuditEvent = AuditEvent
    mod.AuditJournal = AuditJournal
    return mod


def _reaper_stub():
    mod = types.ModuleType("clouddog.reaper")

    @dataclass
    class ReapReport:
        reaped: list = field(default_factory=list)
        mismatches: list = field(default_factory=list)
        skipped: list = field(default_factory=list)
        freed_bytes: int = 0
        halted: bool = False
        halt_reason: str = ""

    class Reaper:
        def __init__(self, src_dir, manifest, log_path, state_path=None,
                     hash_algo="git-sha1", spot_check=None, spot_every=20):
            self.src_dir = src_dir
            self.manifest = manifest
            self.log_path = log_path
            self.hash_algo = hash_algo
            self.dry_runs = []

        def reap_once(self, dry_run=False):
            self.dry_runs.append(dry_run)
            return ReapReport()

    mod.ReapReport = ReapReport
    mod.Reaper = Reaper
    return mod


def _backend_stub_module(modname: str, classname: str, backend_name: str):
    mod = types.ModuleType(modname)

    class _StubBackend(StoreBackend):
        name = backend_name

        def __init__(self):
            self.calls = []
            self._health = HealthStatus(Health.LIVE, "stub")
            self._verify_result = True

        def health(self):
            return self._health

        def store(self, local_path, remote_ref, dry_run=False):
            self.calls.append((local_path, remote_ref, dry_run))
            size = os.path.getsize(local_path) if os.path.isfile(local_path) else 0
            locator = (remote_ref + "#dry-run") if dry_run else remote_ref
            return StoredRef(backend=self.name, locator=locator, size=size,
                             sha256="f" * 64, extra={"dry_run": dry_run})

        def verify(self, local_path, stored):
            return self._verify_result

    _StubBackend.__name__ = classname
    _StubBackend.__qualname__ = classname
    setattr(mod, classname, _StubBackend)
    return mod


policy_mod = _sibling("clouddog.policy", _policy_stub)
audit_mod = _sibling("clouddog.audit", _audit_stub)
reaper_mod = _sibling("clouddog.reaper", _reaper_stub)
github_mod = _sibling("clouddog.stores.github",
                      lambda: _backend_stub_module("clouddog.stores.github",
                                                   "GitHubReleaseBackend", "github"))
terabox_mod = _sibling("clouddog.stores.terabox",
                       lambda: _backend_stub_module("clouddog.stores.terabox",
                                                    "TeraBoxBackend", "terabox"))
drive_mod = _sibling("clouddog.stores.drive",
                     lambda: _backend_stub_module("clouddog.stores.drive",
                                                  "DriveBackend", "drive"))


# ------------------------------------------------------------------ helpers

class FakeBackend:
    """Controllable backend for CLI tests (plain class is enough for the CLI)."""

    def __init__(self, health=Health.LIVE, verify_result=True):
        self._health = HealthStatus(health, "fake")
        self._verify_result = verify_result
        self.store_calls = []
        self.instance = None

    def health(self):
        return self._health

    def store(self, local_path, remote_ref, dry_run=False):
        self.store_calls.append(
            {"local": local_path, "remote_ref": remote_ref, "dry_run": dry_run})
        size = os.path.getsize(local_path)
        return StoredRef(backend="github", locator=remote_ref, size=size,
                         sha256="0" * 64, extra={"dry_run": dry_run})

    def verify(self, local_path, stored):
        return self._verify_result


def _journal_kinds(path):
    kinds = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    kinds.append(json.loads(line).get("kind"))
    return kinds


def _write(tmp_path, name, content=b"hello clouddog"):
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


# --------------------------------------------------------------------- scan

def test_scan_json_uses_monkeypatched_policy_scan(tmp_path, monkeypatch, capsys):
    entries = [
        {"path": "/tmp/a.iso", "bytes": 10, "action": "store",
         "tier": "github", "reason": "big"},
        {"path": "/tmp/b.iso", "bytes": 20, "action": "store_and_clear",
         "tier": "terabox", "reason": "bigger"},
    ]
    monkeypatch.setattr(policy_mod, "scan", lambda root, max_entries=200: entries)
    journal = str(tmp_path / "audit.jsonl")

    rc = main(["--journal", journal, "scan", "--path", "/tmp", "--json"])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out == entries


def test_scan_human_table(tmp_path, monkeypatch, capsys):
    entries = [{"path": "/tmp/a.iso", "bytes": 10, "action": "store",
                "tier": "github", "reason": "big"}]
    monkeypatch.setattr(policy_mod, "scan", lambda root, max_entries=200: entries)

    rc = main(["--journal", str(tmp_path / "audit.jsonl"),
               "scan", "--path", "/tmp", "--max", "50"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "PATH" in out and "/tmp/a.iso" in out and "store" in out


def test_scan_missing_path_arg_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["scan"])
    assert exc.value.code == 2


# -------------------------------------------------------------------- store

def test_store_dry_run_passes_dry_run_true_to_backend(tmp_path, monkeypatch, capsys):
    fake = FakeBackend(health=Health.DEGRADED)  # degraded is OK under --dry-run
    monkeypatch.setattr(github_mod, "GitHubReleaseBackend", lambda: fake)
    target = _write(tmp_path, "blob.bin", b"x" * 100)
    journal = str(tmp_path / "audit.jsonl")

    rc = main(["--dry-run", "--journal", journal, "store", target,
               "--tier", "github", "--remote-ref", "owner/repo@tag"])

    assert rc == 0
    assert len(fake.store_calls) == 1
    assert fake.store_calls[0]["dry_run"] is True
    stored = json.loads(capsys.readouterr().out)
    assert stored["size"] == 100
    assert "stored" in _journal_kinds(journal)


def test_store_refuses_when_backend_not_live(tmp_path, monkeypatch, capsys):
    fake = FakeBackend(health=Health.DEGRADED)
    monkeypatch.setattr(github_mod, "GitHubReleaseBackend", lambda: fake)
    target = _write(tmp_path, "blob.bin")
    journal = str(tmp_path / "audit.jsonl")

    rc = main(["--journal", journal, "store", target,
               "--tier", "github", "--remote-ref", "owner/repo@tag"])

    assert rc == 3
    assert fake.store_calls == []
    err = capsys.readouterr().err
    assert "not LIVE" in err


def test_store_live_backend_journals_stored_ref(tmp_path, monkeypatch, capsys):
    fake = FakeBackend(health=Health.LIVE)
    monkeypatch.setattr(github_mod, "GitHubReleaseBackend", lambda: fake)
    target = _write(tmp_path, "blob.bin", b"y" * 42)
    journal = str(tmp_path / "audit.jsonl")

    rc = main(["--journal", journal, "store", target,
               "--tier", "github", "--remote-ref", "owner/repo@tag"])

    assert rc == 0
    assert fake.store_calls[0]["dry_run"] is False
    stored = json.loads(capsys.readouterr().out)
    assert stored["size"] == 42 and stored["locator"] == "owner/repo@tag"
    assert "stored" in _journal_kinds(journal)


# --------------------------------------------------------------------- reap

class _FakeReaper:
    def __init__(self, src_dir, manifest, log_path, **kwargs):
        self.src_dir = src_dir
        self.manifest = manifest
        self.log_path = log_path
        self.kwargs = kwargs
        self.dry_runs = []

    def reap_once(self, dry_run=False):
        self.dry_runs.append(dry_run)
        report_cls = reaper_mod.ReapReport
        return report_cls(reaped=["a.txt"], mismatches=[], skipped=[],
                          freed_bytes=123, halted=False, halt_reason="")


def test_reap_runs_pass_and_journals_reaped(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(reaper_mod, "Reaper", _FakeReaper)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"a.txt": "deadbeef"}))
    journal = str(tmp_path / "audit.jsonl")

    rc = main(["--journal", journal, "reap",
               "--manifest", str(manifest), "--src-dir", str(tmp_path)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "reaped:      1" in out
    assert "reaped" in _journal_kinds(journal)


def test_reap_halted_returns_3(tmp_path, monkeypatch):
    class _HaltedReaper(_FakeReaper):
        def reap_once(self, dry_run=False):
            report_cls = reaper_mod.ReapReport
            return report_cls(reaped=[], mismatches=["a.txt"], skipped=[],
                              freed_bytes=0, halted=True, halt_reason="boom")

    monkeypatch.setattr(reaper_mod, "Reaper", _HaltedReaper)
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"a.txt": "deadbeef"}))

    rc = main(["--journal", str(tmp_path / "audit.jsonl"), "reap",
               "--manifest", str(manifest), "--src-dir", str(tmp_path)])
    assert rc == 3


# ------------------------------------------------------------------- verify

def test_verify_match_true(tmp_path, monkeypatch, capsys):
    fake = FakeBackend(health=Health.LIVE, verify_result=True)
    monkeypatch.setattr(github_mod, "GitHubReleaseBackend", lambda: fake)
    target = _write(tmp_path, "blob.bin")

    rc = main(["verify", target, "--backend", "github", "--locator", "L",
               "--size", "13", "--sha256", "ab" * 32])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["match"] is True


def test_verify_mismatch_returns_3(tmp_path, monkeypatch, capsys):
    fake = FakeBackend(health=Health.LIVE, verify_result=False)
    monkeypatch.setattr(github_mod, "GitHubReleaseBackend", lambda: fake)
    target = _write(tmp_path, "blob.bin")

    rc = main(["verify", target, "--backend", "github", "--locator", "L"])

    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["match"] is False


# ------------------------------------------------------------------- daemon

def test_daemon_once_runs_single_tick(tmp_path, monkeypatch, capsys):
    import clouddog.daemon as daemon_mod

    calls = []

    class _FakeDaemon:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def tick(self):
            return daemon_mod.TickReport(
                started_utc="t", disk_before_pct=10.0, disk_after_pct=9.0,
                stored=1, verified=1, cleared=0, freed_bytes=0,
                skipped_sacred=0, errors=[], critical=False)

        def run_forever(self):
            raise AssertionError("should not run forever with --once")

    monkeypatch.setattr(daemon_mod, "Daemon", _FakeDaemon)

    rc = main(["--journal", str(tmp_path / "audit.jsonl"),
               "daemon", "--once", "--roots", "/tmp,/var", "--interval", "30"])

    assert rc == 0
    assert len(calls) == 1
    assert calls[0]["roots"] == ["/tmp", "/var"]
    assert calls[0]["interval_s"] == 30
    assert "stored:        1" in capsys.readouterr().out


# ---------------------------------------------------------------- dashboard

def test_dashboard_serves_on_configured_port(monkeypatch):
    server_mod = importlib.import_module("clouddog.dashboard.server")
    served = []
    monkeypatch.setattr(server_mod, "serve", lambda port=8899: served.append(port))

    rc = main(["dashboard", "--port", "8899"])

    assert rc == 0
    assert served == [8899]


def test_dashboard_missing_module_returns_3(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "clouddog.dashboard.server", None)

    rc = main(["dashboard"])

    assert rc == 3
    assert "dashboard" in capsys.readouterr().err.lower()


# ------------------------------------------------------- external tools

def test_find_tool_prefers_managed_install(monkeypatch, tmp_path):
    import clouddog.cli as cli_mod

    fake_bin = tmp_path / "dust"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setitem(cli_mod.MANAGED_TOOLS, "dust", str(fake_bin))
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _n: None)

    assert cli_mod.find_tool("dust") == str(fake_bin)


def test_find_tool_missing_returns_none(monkeypatch):
    import clouddog.cli as cli_mod

    monkeypatch.setitem(cli_mod.MANAGED_TOOLS, "nosuchtool", "/nonexistent/x")
    monkeypatch.setattr(cli_mod.shutil, "which", lambda _n: None)

    assert cli_mod.find_tool("nosuchtool") is None


def test_du_missing_tool_returns_3(monkeypatch, capsys):
    import clouddog.cli as cli_mod

    monkeypatch.setattr(cli_mod, "find_tool", lambda _n: None)

    rc = main(["du", "--path", "/tmp"])
    assert rc == 3
    assert "dust" in capsys.readouterr().err.lower()


def test_du_runs_dust(monkeypatch):
    import clouddog.cli as cli_mod
    import subprocess as sp

    calls = []

    class FakeProc:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr(cli_mod, "find_tool", lambda _n: "/fake/dust")
    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)

    rc = main(["du", "--path", "/tmp", "--depth", "2"])
    assert rc == 0
    assert calls[0][:3] == ["/fake/dust", "-d", "2"]


def test_dedupe_reports_groups_and_never_deletes(monkeypatch, capsys, tmp_path):
    import clouddog.cli as cli_mod

    payload = {
        "groups": [
            {"file_len": 2000, "file_hash": "abc",
             "files": ["/a/x.bin", "/b/x.bin"]},
            {"file_len": 100, "file_hash": "def",
             "files": ["/a/y.bin"]},  # single file -> not a group
        ]
    }

    class FakeProc:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return FakeProc()

    monkeypatch.setattr(cli_mod, "find_tool", lambda _n: "/fake/fclones")
    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)

    rc = main(["--journal", str(tmp_path / "audit.jsonl"),
               "dedupe", "--path", "/tmp", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["wasted_bytes"] == 2000
    assert len(out["groups"]) == 1
    assert out["groups"][0]["count"] == 2
    # report only: the fclones command has no delete/remove flag
    assert not any("delete" in c or "remove" in c for c in seen[0])


def test_dedupe_dict_style_files_parsed(monkeypatch, capsys, tmp_path):
    import clouddog.cli as cli_mod

    payload = {"groups": [{"file_len": 50, "files": [{"path": "/a/1"},
                                                     {"path": "/b/1"}]}]}

    class FakeProc:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(cli_mod, "find_tool", lambda _n: "/fake/fclones")
    monkeypatch.setattr(cli_mod.subprocess, "run",
                        lambda cmd, **kw: FakeProc())

    rc = main(["--journal", str(tmp_path / "audit.jsonl"),
               "dedupe", "--path", "/tmp", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["groups"]) == 1
