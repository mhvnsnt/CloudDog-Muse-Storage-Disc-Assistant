"""Tests for clouddog.delete_gate — the hard no-unlink invariant.

Incident regression anchor (2026-10-20): the daemon deleted
~/workspace/tripo-wire-20261019 with no registry entry and no live
verification. The gate exists so that class of delete is impossible:
ALL of registry + live verify + claims + policy must hold at delete
time, and every block is reported per-leg so callers can journal it.

The daemon-level incident test (a no-registry file surviving a full
--apply tick) lives in test_daemon.py; the gate legs are isolated here.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime, timezone

from clouddog.delete_gate import check_delete_gate

PATH = "/some/work/file.bin"
DATA = b"payload" * 128
SHA = hashlib.sha256(DATA).hexdigest()
SIZE = len(DATA)


def _entry(**over):
    entry = {"store": "github", "sha256": SHA, "size": SIZE, "status": "registered"}
    entry.update(over)
    return entry


def _free(_path):
    return False, ""


def _claimed(_path):
    return True, "claimed by lane-x (store) heartbeat fresh"


def _boom(_path):
    raise RuntimeError("claims db locked")


def _verdict(**over):
    kw = {
        "registry_entry": _entry(),
        "local_sha256": SHA,
        "local_size": SIZE,
        "live_verify_ok": True,
        "claim_check": _free,
        "policy_action": "store_and_clear",
    }
    kw.update(over)
    return check_delete_gate(PATH, **kw)


def _legs(verdict):
    return {b.leg for b in verdict.blocks}


def test_all_legs_hold_allows_delete():
    v = _verdict()
    assert v.allowed is True
    assert v.blocks == ()
    assert "all legs hold" in v.summary


def test_no_registry_entry_blocks():
    v = _verdict(registry_entry=None)
    assert v.allowed is False
    assert "registry" in _legs(v)
    assert "no registry entry" in v.summary


def test_reaped_entry_blocks():
    v = _verdict(registry_entry=_entry(status="reaped"))
    assert v.allowed is False
    assert "registry" in _legs(v)


def test_registry_sha_mismatch_blocks():
    v = _verdict(local_sha256=hashlib.sha256(b"other").hexdigest())
    assert v.allowed is False
    assert "registry" in _legs(v)
    assert any("sha256" in b.reason for b in v.blocks)


def test_registry_size_mismatch_blocks():
    v = _verdict(local_size=SIZE + 1)
    assert v.allowed is False
    assert "registry" in _legs(v)
    assert any("size" in b.reason for b in v.blocks)


def test_registry_entry_without_hash_blocks():
    entry = _entry()
    del entry["sha256"]
    v = _verdict(registry_entry=entry)
    assert v.allowed is False
    assert "registry" in _legs(v)


def test_registry_digest_binding_for_reaper_style_entries():
    import hashlib as _h

    digest = _h.sha1(b"blob %d\0" % SIZE + DATA).hexdigest()
    entry = {"digest": digest, "digest_algo": "git-sha1", "size": SIZE, "status": "registered"}
    v = _verdict(
        registry_entry=entry, local_sha256="", local_digest=digest, local_digest_algo="git-sha1"
    )
    assert v.allowed is True
    v2 = _verdict(
        registry_entry=entry, local_sha256="", local_digest="0" * 40, local_digest_algo="git-sha1"
    )
    assert v2.allowed is False
    assert "registry" in _legs(v2)


def test_no_live_verify_blocks():
    v = _verdict(live_verify_ok=False)
    assert v.allowed is False
    assert _legs(v) == {"verify"}


def test_active_claim_blocks():
    v = _verdict(claim_check=_claimed)
    assert v.allowed is False
    assert _legs(v) == {"claims"}
    assert "lane-x" in v.summary


def test_claims_checker_missing_fails_closed():
    v = _verdict(claim_check=None)
    assert v.allowed is False
    assert _legs(v) == {"claims"}


def test_claims_checker_raising_fails_closed():
    v = _verdict(claim_check=_boom)
    assert v.allowed is False
    assert _legs(v) == {"claims"}
    assert "failing closed" in v.summary


def test_policy_sacred_blocks():
    v = _verdict(policy_action="sacred")
    assert v.allowed is False
    assert _legs(v) == {"policy"}


def test_policy_sacred_blocks_even_if_caller_whitelists_it():
    v = _verdict(policy_action="sacred", deletable_actions=frozenset({"sacred", "store_and_clear"}))
    assert v.allowed is False
    assert "policy" in _legs(v)


def test_policy_keep_local_blocks():
    v = _verdict(policy_action="keep_local")
    assert v.allowed is False
    assert _legs(v) == {"policy"}


def test_policy_strict_blocks_for_daemon_deletable_set():
    v = _verdict(policy_action="store_and_clear_strict")
    assert v.allowed is False
    assert _legs(v) == {"policy"}


def test_policy_unknown_action_blocks():
    v = _verdict(policy_action="")
    assert v.allowed is False
    assert "policy" in _legs(v)


def test_multiple_failures_all_reported():
    v = _verdict(
        registry_entry=None, live_verify_ok=False, claim_check=_claimed, policy_action="sacred"
    )
    assert v.allowed is False
    assert _legs(v) == {"registry", "verify", "claims", "policy"}


# ============================================================ daemon level
# The incident itself, replayed: a daemon --apply run over a directory
# holding a store_and_clear file that has NO registry entry. Before the
# gate, the daemon's delete authority came from its own tick state and
# the file died unregistered. Now it must survive, the block must be
# journaled, and the daemon's store must land in the registry instead.

from clouddog import registry as registry_mod
from clouddog.daemon import Daemon
from clouddog.stores.base import Health, HealthStatus, StoreBackend, StoredRef

BAD_LOCATOR = "other/repo@old-tag/big.bin"


class _Backend(StoreBackend):
    name = "fake"

    def health(self):
        return HealthStatus(Health.LIVE, "fake")

    def store(self, local_path, remote_ref, dry_run=False):
        with open(local_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return StoredRef(
            backend="fake",
            locator=remote_ref,
            size=os.path.getsize(local_path),
            sha256=digest,
            extra={},
        )

    def verify(self, local_path, stored):
        # The copy this run just stored verifies; a registry entry
        # pointing anywhere else does not (test 4's lever).
        return stored.locator != BAD_LOCATOR


class _Journal:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)

    def read(self, limit=100):
        return list(self.events[-limit:])

    def notes(self, kind):
        return [e.note for e in self.events if getattr(e, "kind", "") == kind]


def _aged_file(root, name="big.bin", size=11 * 1024 * 1024):
    p = root / name
    p.write_bytes(b"x" * size)
    old = time.time() - 7200  # cold: 2h old, over the 10MB floor
    os.utime(p, (old, old))
    return p


def _daemon(tmp_path, journal, registry_json):
    # Seed earned APPLY trust in tmp (training layer): the incident
    # replay is about the delete GATE, and a fresh trust file would
    # (correctly) force dry-run before the gate is ever reached.
    from clouddog import trust as _trust

    tp = str(tmp_path / "trust.json")
    seeded = _trust.load_trust(tp)
    seeded["state"] = _trust.APPLY
    _trust.save_trust(seeded, tp)
    return Daemon(
        roots=[str(tmp_path)],
        backends={"github": _Backend()},
        journal=journal,
        disk_path=str(tmp_path),
        dry_run=False,  # --apply, like the incident run
        registry_path=str(registry_json),
        registry_db_path=str(tmp_path / "no-registry.db"),
        claims_db=str(tmp_path / "claims.db"),
        trust_path=tp,
    )


def _claims_db_with_live_claim(db_path, claimed_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE claims (path TEXT PRIMARY KEY, owner TEXT NOT NULL,"
        " action TEXT NOT NULL, claim_id TEXT NOT NULL UNIQUE,"
        " epoch INTEGER NOT NULL, claimed_at TEXT NOT NULL,"
        " heartbeat_at TEXT NOT NULL, ttl_s INTEGER NOT NULL DEFAULT 600,"
        " note TEXT NOT NULL DEFAULT '')"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
        (str(claimed_path), "beat-e-lane", "store", "c1", 1, now, now, 3600, ""),
    )
    conn.commit()
    conn.close()


def test_incident_no_registry_entry_survives_daemon_apply(tmp_path):
    target = _aged_file(tmp_path)
    journal = _Journal()
    registry_json = tmp_path / "registry.json"  # starts absent: NO entry
    daemon = _daemon(tmp_path, journal, registry_json)

    report = daemon.tick()

    # The file survives a full --apply tick, untouched.
    assert target.exists()
    assert report.cleared == 0
    # ...and the block is journaled with the registry reason.
    blocked = journal.notes("delete_blocked")
    assert blocked and "registry" in blocked[0]
    # The daemon's verified store was registered instead of deleted on
    # sight (two-pass rule): the registry now has the entry.
    entry = registry_mod.lookup_entry(
        str(target), json_path=str(registry_json), db_path=str(tmp_path / "no.db")
    )
    assert entry is not None
    assert entry["sha256"] == hashlib.sha256(b"x" * target.stat().st_size).hexdigest()

    # A later pass may clear it: entry pre-exists, binds to the bytes,
    # live verify runs again, no claims, policy still store_and_clear.
    report2 = daemon.tick()
    assert report2.cleared == 1
    assert not target.exists()
    assert journal.notes("cleared")


def test_daemon_claimed_registered_file_survives(tmp_path):
    target = _aged_file(tmp_path)
    registry_json = tmp_path / "registry.json"
    data = target.read_bytes()
    registry_mod.register_entry(
        str(target),
        {
            "store": "github",
            "repo": "mhvnsnt/clouddog-storage",
            "release": "clouddog-20261020",
            "asset": "big.bin",
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
        json_path=str(registry_json),
    )
    _claims_db_with_live_claim(tmp_path / "claims.db", target)
    journal = _Journal()
    daemon = _daemon(tmp_path, journal, registry_json)

    report = daemon.tick()

    assert target.exists()
    assert report.cleared == 0
    blocked = journal.notes("delete_blocked")
    assert blocked and "claims" in blocked[0]


def test_daemon_entry_sha_mismatch_survives(tmp_path):
    target = _aged_file(tmp_path)
    registry_json = tmp_path / "registry.json"
    registry_mod.register_entry(
        str(target),
        {
            "store": "github",
            "repo": "mhvnsnt/clouddog-storage",
            "release": "clouddog-20261020",
            "asset": "big.bin",
            "sha256": "0" * 64,
            "size": target.stat().st_size,
        },
        json_path=str(registry_json),
    )
    journal = _Journal()
    daemon = _daemon(tmp_path, journal, registry_json)

    report = daemon.tick()

    assert target.exists()
    assert report.cleared == 0
    blocked = journal.notes("delete_blocked")
    assert blocked and "registry" in blocked[0] and "sha256" in blocked[0]


def test_daemon_entry_copy_failing_live_verify_survives(tmp_path):
    target = _aged_file(tmp_path)
    registry_json = tmp_path / "registry.json"
    data = target.read_bytes()
    registry_mod.register_entry(
        str(target),
        {
            "store": "github",
            "locator": BAD_LOCATOR,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        },
        json_path=str(registry_json),
    )
    journal = _Journal()
    daemon = _daemon(tmp_path, journal, registry_json)

    report = daemon.tick()

    assert target.exists()
    assert report.cleared == 0
    blocked = journal.notes("delete_blocked")
    assert blocked and "verify" in blocked[0]
    # The registry JSON on disk is exactly what was registered (the
    # daemon did not rewrite the entry to make the gate pass).
    on_disk = json.loads(registry_json.read_text())
    assert on_disk["entries"][str(target)]["locator"] == BAD_LOCATOR


# ============================================================ reaper level

from clouddog.reaper import Reaper, compute_hash


def _reaper(tmp_path, rel="a.bin", data=b"reaper-gate-data", **kw):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    p = src / rel
    p.write_bytes(data)
    manifest = {rel: compute_hash(str(p), "git-sha1")}
    kw.setdefault("store_verify", lambda r, full: True)
    kw.setdefault("claim_check", lambda path: (False, ""))
    r = Reaper(
        str(src),
        manifest,
        str(tmp_path / "reaper.log"),
        state_path=str(tmp_path / "state.json"),
        **kw,
    )
    return r, p


def test_reaper_policy_sacred_blocks_despite_verified_store(tmp_path):
    # A .DO_NOT_DELETE marker over the tree makes evaluate() SACRED;
    # manifest + live store verify must NOT be enough to delete.
    src = tmp_path / "src"
    src.mkdir()
    (src / ".DO_NOT_DELETE").write_text("lane working here")
    r, p = _reaper(tmp_path)
    report = r.reap_once(dry_run=False)
    assert p.exists()
    assert report.reaped == 0
    with open(tmp_path / "reaper.log") as fh:
        assert "DELETE-BLOCKED" in fh.read()


def test_reaper_default_claims_wiring_blocks_claimed_file(tmp_path):
    # No claim_check passed: the reaper must wire the production claims
    # checker itself (there is no gate-less mode after the incident fix).
    claims_db = tmp_path / "claims.db"
    src = tmp_path / "src"
    src.mkdir()
    p = src / "a.bin"
    p.write_bytes(b"claimed-data")
    _claims_db_with_live_claim(claims_db, p)
    manifest = {"a.bin": compute_hash(str(p), "git-sha1")}
    r = Reaper(
        str(src),
        manifest,
        str(tmp_path / "reaper.log"),
        state_path=str(tmp_path / "state.json"),
        store_verify=lambda rel, full: True,
        claims_db=str(claims_db),
    )
    report = r.reap_once(dry_run=False)
    assert p.exists()
    assert report.reaped == 0


def test_reaper_policy_check_injected_sacred_blocks(tmp_path):
    r, p = _reaper(tmp_path, policy_check=lambda path: "sacred")
    report = r.reap_once(dry_run=False)
    assert p.exists()
    assert report.reaped == 0


def test_reaper_all_legs_hold_still_reaps(tmp_path):
    r, p = _reaper(tmp_path)
    report = r.reap_once(dry_run=False)
    assert report.reaped == 1
    assert not p.exists()


# ========================================================== streamer level

from clouddog.streamer import Streamer


class _StreamBackend:
    name = "fake"

    def store(self, local_path, remote_ref, dry_run=False):
        with open(local_path, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        return StoredRef(
            backend="fake",
            locator=f"{remote_ref}/{os.path.basename(local_path)}",
            size=os.path.getsize(local_path),
            sha256=digest,
            extra={},
        )

    def verify(self, local_path, stored):
        return True


def test_streamer_claimed_file_streams_but_never_clears(tmp_path):
    wd = tmp_path / "watch"
    wd.mkdir()
    p = wd / "landed.bin"
    p.write_bytes(b"s" * (2 * 1024 * 1024))
    old = time.time() - 7200
    os.utime(p, (old, old))
    events = []
    streamer = Streamer(
        str(wd),
        _StreamBackend(),
        repo="o/r",
        tag="t1",
        settle_secs=300,
        apply=True,
        journal=lambda *a: events.append(a),
        claim_check=lambda path: (True, "claimed by download-lane"),
    )
    t0 = time.time()
    streamer.stream_once(now=t0)  # first sighting: settling
    report = streamer.stream_once(now=t0 + 301)  # settled: streams
    assert p.exists()  # streamed + verified, but the claim blocks the clear
    assert report.files_cleared == 0
    assert report.files_blocked >= 1
    kinds = [e[0] for e in events]
    assert "delete_blocked" in kinds
    notes = [e[4] for e in events if e[0] == "delete_blocked"]
    assert "claims" in notes[0]
