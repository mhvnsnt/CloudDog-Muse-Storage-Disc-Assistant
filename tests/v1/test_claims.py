"""Tests for clouddog.claims (read-side claim checks) and the Reaper gate."""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from clouddog import claims as claims_mod
from clouddog.claims import check_claimed, make_claim_checker
from clouddog.reaper import Reaper


def git_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def write_claim_db(dp, path, owner="lane-a", action="download", ttl_s=600, seconds_ago=0):
    """Create a minimal claims-store sqlite DB with one live claim row."""
    now = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    conn = sqlite3.connect(dp)
    try:
        conn.execute(
            "CREATE TABLE claims (path TEXT PRIMARY KEY, owner TEXT NOT NULL,"
            " action TEXT NOT NULL, claim_id TEXT NOT NULL UNIQUE,"
            " epoch INTEGER NOT NULL, claimed_at TEXT NOT NULL,"
            " heartbeat_at TEXT NOT NULL, ttl_s INTEGER NOT NULL DEFAULT 600,"
            " note TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
            (path, owner, action, "cid-1", 1, now, now, ttl_s, ""),
        )
        conn.commit()
    finally:
        conn.close()


def test_check_claimed_live_claim(tmp_path):
    dp = str(tmp_path / "claims.db")
    write_claim_db(dp, str(tmp_path / "work"), owner="t5-iso-dl")
    blocked, detail = check_claimed(dp, str(tmp_path / "work" / "tekken5.iso"))
    assert blocked and "t5-iso-dl" in detail
    blocked, _ = check_claimed(dp, str(tmp_path / "other" / "file"))
    assert not blocked


def test_check_claimed_expired_is_free(tmp_path):
    dp = str(tmp_path / "claims.db")
    write_claim_db(dp, str(tmp_path / "work"), ttl_s=60, seconds_ago=3600)
    blocked, _ = check_claimed(dp, str(tmp_path / "work" / "f"))
    assert not blocked


def test_check_claimed_missing_db_and_pre_claims_db_are_free(tmp_path):
    blocked, _ = check_claimed(str(tmp_path / "no-such.db"), "/w/f")
    assert not blocked
    dp = str(tmp_path / "old.db")
    conn = sqlite3.connect(dp)
    conn.execute("CREATE TABLE entries (a TEXT)")
    conn.commit()
    conn.close()
    blocked, _ = check_claimed(dp, "/w/f")
    assert not blocked
    # Migration (2026-10-20 fix): the pre-claims DB GAINED the claims
    # table as a side effect — the gate now reads a real store instead
    # of treating a missing table as "free" (the writer side never did).
    conn = sqlite3.connect(dp)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "claims" in tables


def test_check_claimed_unopenable_db_fails_closed(tmp_path):
    # A path that exists but cannot be opened as a DB at all (here: a
    # directory) is unreadable — fail closed, never "free".
    blocked, detail = check_claimed(str(tmp_path), "/w/f")
    assert blocked and "failing closed" in detail


def test_check_claimed_corrupt_db_fails_closed(tmp_path):
    dp = str(tmp_path / "corrupt.db")
    with open(dp, "wb") as f:
        f.write(b"garbage" * 128)
    blocked, detail = check_claimed(dp, "/w/f")
    assert blocked and "failing closed" in detail


def test_make_claim_checker_binds_db(tmp_path):
    dp = str(tmp_path / "claims.db")
    write_claim_db(dp, "/w/f")
    check = make_claim_checker(dp)
    blocked, detail = check("/w/f")
    assert blocked and "lane-a" in detail
    assert check("/w/other") == (False, "")


def _reap_setup(tmp_path, claim_check=None):
    src = tmp_path / "src"
    src.mkdir()
    p = src / "a.bin"
    p.write_bytes(b"hello-claims")
    manifest = {"a.bin": git_sha(b"hello-claims")}
    log_path = str(tmp_path / "reaper.log")
    state_path = str(tmp_path / "reaper_state.json")
    r = Reaper(
        str(src),
        manifest,
        log_path,
        state_path=state_path,
        store_verify=lambda rel, full: True,
        claim_check=claim_check,
    )
    return r, p


def test_reaper_skips_claimed_file(tmp_path):
    dp = str(tmp_path / "claims.db")
    src = tmp_path / "src"
    write_claim_db(dp, str(src), owner="dl-lane")
    r, p = _reap_setup(tmp_path, claim_check=make_claim_checker(dp))
    report = r.reap_once(dry_run=False)
    assert p.exists()  # NOT deleted
    assert report.reaped == 0 and report.skipped == 1
    with open(r._log_path) as fh:
        assert "CLAIMED-SKIP a.bin" in fh.read()


def test_reaper_fails_closed_when_checker_raises(tmp_path):
    def boom(_path):
        raise RuntimeError("store on fire")

    r, p = _reap_setup(tmp_path, claim_check=boom)
    report = r.reap_once(dry_run=False)
    assert p.exists()
    assert report.reaped == 0
    with open(r._log_path) as fh:
        assert "CLAIMS-UNREADABLE a.bin" in fh.read()


def test_reaper_reaps_when_free(tmp_path):
    dp = str(tmp_path / "claims.db")
    write_claim_db(dp, str(tmp_path / "unrelated"), owner="other")
    r, p = _reap_setup(tmp_path, claim_check=make_claim_checker(dp))
    report = r.reap_once(dry_run=False)
    assert not p.exists()
    assert report.reaped == 1


def test_reaper_without_checker_keeps_legacy_behavior(tmp_path):
    r, p = _reap_setup(tmp_path, claim_check=None)
    report = r.reap_once(dry_run=False)
    assert not p.exists()
    assert report.reaped == 1


def test_module_cli_main_exit_codes(tmp_path, monkeypatch):
    dp = str(tmp_path / "claims.db")
    write_claim_db(dp, str(tmp_path / "work"))
    monkeypatch.setattr(claims_mod, "default_claims_db", lambda: dp)
    assert claims_mod.main(["check", str(tmp_path / "work" / "f")]) == 0
    assert claims_mod.main(["check", str(tmp_path / "elsewhere")]) == 1
