"""Integration tests for the CloudDog training layer (2026-10-20):
daemon trust gate, trust promotion through ticks, mirror-coverage
findings in ticks, heartbeat correctness fields, the scorecard CLI,
guardian/root audit grading, and state-dir isolation.

Hard rule (deleter audit): tests NEVER touch the real ~/.clouddog —
trust/heartbeat/journal resolve through $CLOUDEDOG_STATE_DIR, which
tests/v1/conftest.py points at a per-test tmp dir.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import shutil
import time

from clouddog import cli, scorecard, trust
from clouddog.audit import AuditJournal
from clouddog.daemon import Daemon
from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef

Usage = collections.namedtuple("usage", "total used free")


class FakeBackend(StoreBackend):
    name = "fake"

    def __init__(self):
        self.store_calls = []

    def health(self):
        return HealthStatus(Health.LIVE, "fake")

    def store(self, local_path, remote_ref, dry_run=False):
        self.store_calls.append({"dry_run": dry_run})
        with open(local_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return StoredRef(backend="fake", locator=remote_ref,
                         size=os.path.getsize(local_path), sha256=digest, extra={})

    def verify(self, local_path, stored):
        return True


class FakeJournal:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def read(self, limit=100):
        return list(self.events[-limit:])

    def kinds(self):
        return [e.kind if hasattr(e, "kind") else e[1] for e in self.events]

    def grades(self):
        return [getattr(e, "grade", "") for e in self.events
                if getattr(e, "kind", "") == "scorecard"]


def _low_disk(monkeypatch):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(1000, 100, 900))


def _seed_trust(tmp_path, state, required=20):
    tp = str(tmp_path / "trust.json")
    s = trust.load_trust(tp, required_streak=required)
    s["state"] = state
    trust.save_trust(s, tp)
    return tp


def _entry_for(path):
    return [{"path": path, "bytes": os.path.getsize(path),
             "action": "store_and_clear", "tier": "github", "reason": "test"}]


def _daemon(tmp_path, monkeypatch, entries, trust_path, **kw):
    import clouddog.policy as policy_mod

    monkeypatch.setattr(policy_mod, "scan", lambda root, max_entries=200: list(entries))
    from clouddog.policy import Action, Decision

    monkeypatch.setattr(
        policy_mod, "evaluate",
        lambda p, scan_root=None: Decision(Action.STORE_AND_CLEAR, "github", "test"),
    )
    kw.setdefault("registry_path", str(tmp_path / "registry.json"))
    kw.setdefault("registry_db_path", str(tmp_path / "registry.db"))
    kw.setdefault("claims_db", str(tmp_path / "claims.db"))
    d = Daemon(roots=[str(tmp_path)], backends={"github": FakeBackend()},
               journal=FakeJournal(), disk_path=str(tmp_path),
               trust_path=trust_path, **kw)
    d.heartbeat_path = str(tmp_path / "heartbeat.json")
    return d


# ------------------------------------------------------------- trust gate


def test_daemon_apply_refused_while_revoked_forces_dry_run(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = tmp_path / "big.bin"
    target.write_bytes(b"data" * 64)
    tp = _seed_trust(tmp_path, trust.REVOKED)
    d = _daemon(tmp_path, monkeypatch, _entry_for(str(target)), tp, dry_run=False)

    report = d.tick()

    assert report.trust_forced_dry_run is True
    assert report.trust_state == trust.PROBATION  # clean dry-run cycle = earning
    assert report.cleared == 0 and report.correct_clears == 0
    assert os.path.exists(target)  # nothing deleted while revoked
    assert "trust_refused_apply" in d.journal.kinds()
    assert scorecard.BLOCKED in d.journal.grades()
    assert d.backends["github"].store_calls[0]["dry_run"] is True


def test_daemon_promotes_through_ticks_and_then_may_apply(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    tp = _seed_trust(tmp_path, trust.REVOKED, required=2)
    d = _daemon(tmp_path, monkeypatch, [], tp, dry_run=False)

    r1 = d.tick()
    assert r1.trust_forced_dry_run is True
    assert trust.current_state(tp) == trust.PROBATION
    r2 = d.tick()
    assert trust.current_state(tp) == trust.APPLY
    r3 = d.tick()
    assert r3.trust_forced_dry_run is False
    assert r3.trust_state == trust.APPLY


def test_daemon_near_miss_revokes_earned_trust(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    target = tmp_path / "big.bin"
    target.write_bytes(b"data" * 64)
    # Pre-register a binding entry, then make ONLY the verify leg fail:
    # entry locator points at a copy the backend did not just store ->
    # _entry_ref mismatch -> backend.verify(locator != stored) — our
    # FakeBackend verifies everything, so instead break the claims leg
    # alone via a live claim (exactly one failing leg = NEAR_MISS).
    from clouddog import registry as reg

    data = target.read_bytes()
    reg.register_entry(str(target), {
        "store": "github", "locator": "mhvnsnt/clouddog-storage@clouddog-x/big.bin",
        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
    }, json_path=str(tmp_path / "registry.json"))
    import sqlite3
    from datetime import datetime, timezone

    db = str(tmp_path / "claims.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE claims (path TEXT PRIMARY KEY, owner TEXT NOT NULL,"
        " action TEXT NOT NULL, claim_id TEXT NOT NULL UNIQUE,"
        " epoch INTEGER NOT NULL, claimed_at TEXT NOT NULL,"
        " heartbeat_at TEXT NOT NULL, ttl_s INTEGER NOT NULL DEFAULT 600,"
        " note TEXT NOT NULL DEFAULT '')")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
                 (str(target), "lane", "store", "c1", 1, now, now, 3600, ""))
    conn.commit()
    conn.close()

    tp = _seed_trust(tmp_path, trust.APPLY)
    d = _daemon(tmp_path, monkeypatch, _entry_for(str(target)), tp, dry_run=False)
    report = d.tick()

    assert os.path.exists(target)
    assert report.near_misses == 1
    assert trust.current_state(tp) == trust.REVOKED  # revoked on the spot
    assert scorecard.NEAR_MISS in d.journal.grades()


def test_daemon_tick_unmirrored_old_breaks_clean_cycle(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    f = scratch / "old-work.bin"
    f.write_bytes(b"x" * 32)
    old = time.time() - 7 * 3600
    os.utime(f, (old, old))
    tp = _seed_trust(tmp_path, trust.PROBATION, required=2)
    trust.record_clean_cycle(tp)  # streak 1 of 2
    d = _daemon(tmp_path, monkeypatch, [], tp, dry_run=True)

    report = d.tick()

    assert report.unmirrored_old == 1
    assert report.clean_cycle is False
    state = trust.load_trust(tp)
    assert state["clean_streak"] == 0  # promotion streak reset
    assert state["state"] == trust.PROBATION  # but not revoked
    assert "unmirrored_old" in d.journal.kinds()
    assert scorecard.UNMIRRORED_OLD in d.journal.grades()


def test_heartbeat_carries_correctness_fields_before_bytes(tmp_path, monkeypatch):
    _low_disk(monkeypatch)
    tp = _seed_trust(tmp_path, trust.APPLY)
    d = _daemon(tmp_path, monkeypatch, [], tp, dry_run=True)
    d.tick()
    payload = json.loads((tmp_path / "heartbeat.json").read_text())
    for field in ("correct_clears", "correct_blocks", "near_misses",
                  "violations", "unmirrored_old", "trust_state"):
        assert field in payload
    assert payload["trust_state"] == trust.APPLY
    keys = list(payload.keys())
    assert keys.index("correct_clears") < keys.index("freed_bytes")
    assert keys.index("trust_state") < keys.index("freed_bytes")


def test_state_dir_isolation_default_paths(tmp_path, monkeypatch):
    # conftest pointed CLOUDEDOG_STATE_DIR at a tmp dir; defaults must
    # resolve inside it, never the real ~/.clouddog.
    state = os.environ["CLOUDEDOG_STATE_DIR"]
    assert trust.default_trust_path() == os.path.join(state, "trust.json")
    d = Daemon(roots=[str(tmp_path)], backends={}, journal=FakeJournal(),
               disk_path=str(tmp_path), dry_run=True)
    assert d.heartbeat_path == os.path.join(state, "heartbeat.json")
    assert d.pidfile == os.path.join(state, "daemon.pid")
    assert not d.heartbeat_path.startswith(os.path.expanduser("~/.clouddog"))


# ------------------------------------------------------------- scorecard CLI


def test_cli_scorecard_orders_correctness_before_bytes(tmp_path, capsys):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = str(tmp_path / "trust.json")
    scorecard.record_grade(journal, scorecard.CORRECT, path="/a", source="daemon",
                           note="cleared through delete gate", trust_path=tp)
    scorecard.record_grade(journal, scorecard.BLOCKED, path="/b", source="daemon",
                           note="gate blocked", trust_path=tp)
    rc = cli.main(["--journal", str(tmp_path / "audit.jsonl"),
                   "scorecard", "--trust", tp])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.index("correct clears") < out.index("correct blocks")
    assert out.index("trust_state") < out.index("freed_bytes")
    assert "correct clears: 1" in out
    assert "correct blocks: 1" in out


def test_cli_scorecard_json_and_root_journal_merge(tmp_path, capsys):
    journal_path = tmp_path / "audit.jsonl"
    AuditJournal(str(journal_path))  # exists, empty
    root_journal = tmp_path / "root-audit.jsonl"
    root_events = [
        {"ts": "2026-10-20T03:00:00+00:00", "action": "reap", "path": "/w/a.bin",
         "store": "github", "size": 4096},
        {"ts": "2026-10-20T03:01:00+00:00", "action": "reap_policy_block",
         "path": "/w/b.bin", "store": "github", "detail": "SACRED"},
        {"ts": "2026-10-20T03:02:00+00:00", "action": "reap_keep",
         "path": "/w/c.bin", "store": "github", "detail": "verify failed"},
        {"ts": "2026-10-20T03:03:00+00:00", "action": "daemon_cycle",
         "disk_pct": 91.0},
    ]
    root_journal.write_text("\n".join(json.dumps(e) for e in root_events) + "\n")
    tp = str(tmp_path / "trust.json")
    rc = cli.main(["--journal", str(journal_path), "--json",
                   "scorecard", "--trust", tp,
                   "--root-journal", str(root_journal)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["correct_clears"] == 1       # the root reap, graded CORRECT
    assert out["correct_blocks"] == 2      # policy block + verify-failed keep
    assert out["freed_bytes"] == 4096      # footnote from the root reap
    assert out["trust_state"] == trust.REVOKED


def test_root_adapter_grades_guardian_actions():
    events = [
        {"action": "reap", "path": "/a", "size": 10},
        {"action": "reap_policy_skip", "path": "/b"},
        {"action": "verify", "path": "/c", "ok": False},
        {"action": "verify", "path": "/d", "ok": True},
        {"action": "reap_policy_unavailable"},
        {"action": "reap_summary", "count": 1, "bytes_freed": 10},  # not gradeable
        {"action": "daemon_start", "pid": 1},                        # not gradeable
    ]
    s = scorecard.summarize_events([], trust_state="REVOKED", root_events=events)
    assert s.correct == 3        # reap + policy-skip-kept + verify-ok
    assert s.correct_clears == 1
    assert s.correct_blocks == 2  # verify-failed keep + policy-unavailable refusal
    assert s.freed_bytes == 10


# ------------------------------------------------------- reaper / streamer


def test_reaper_grades_clears_and_blocks(tmp_path):
    from clouddog.reaper import Reaper, compute_hash

    src = tmp_path / "src"
    src.mkdir()
    good = src / "good.bin"
    good.write_bytes(b"good-data")
    held = src / "held.bin"
    held.write_bytes(b"held-data")
    manifest = {
        "good.bin": compute_hash(str(good), "git-sha1"),
        "held.bin": compute_hash(str(held), "git-sha1"),
    }
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = str(tmp_path / "trust.json")
    r = Reaper(
        str(src), manifest, str(tmp_path / "reaper.log"),
        state_path=str(tmp_path / "state.json"),
        store_verify=lambda rel, full: True,
        claim_check=lambda p: (p.endswith("held.bin"), "claimed" if p.endswith("held.bin") else ""),
        policy_check=lambda p: "store_and_clear",
        trust_path=tp, scorecard_journal=journal,
    )
    report = r.reap_once(dry_run=False)
    assert report.reaped == 1
    assert report.correct_clears == 1
    # held.bin: claims pre-check grades BLOCKED, file survives
    assert report.correct_blocks >= 1
    assert held.exists() and not good.exists()
    grades = [e["grade"] for e in journal.read() if e["kind"] == "scorecard"]
    assert "CORRECT" in grades and "BLOCKED" in grades


def test_streamer_grades_clear_and_near_miss(tmp_path):
    from clouddog.streamer import Streamer

    class Backend:
        name = "fake"

        def store(self, local_path, remote_ref, dry_run=False):
            with open(local_path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            return StoredRef(backend="fake", locator=f"{remote_ref}/{os.path.basename(local_path)}",
                             size=os.path.getsize(local_path), sha256=digest, extra={})

        def verify(self, local_path, stored):
            return True

    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = str(tmp_path / "trust.json")

    def _run(claim):
        wd = tmp_path / ("watch_claimed" if claim else "watch_free")
        wd.mkdir()
        p = wd / "landed.bin"
        p.write_bytes(b"s" * (1024 * 1024))
        old = time.time() - 7200
        os.utime(p, (old, old))
        st = Streamer(str(wd), Backend(), repo="o/r", tag="t", settle_secs=0,
                      apply=True, claim_check=lambda path: (claim, "claimed" if claim else ""),
                      trust_path=tp, scorecard_journal=journal)
        t0 = time.time()
        st.stream_once(now=t0)
        return st.stream_once(now=t0 + 1), p

    report, p = _run(claim=False)
    assert report.correct_clears == 1 and not p.exists()

    report2, p2 = _run(claim=True)
    assert p2.exists()
    # claims is the ONLY failing leg -> NEAR_MISS, trust revoked
    assert report2.near_misses == 1
    assert trust.current_state(tp) == trust.REVOKED
