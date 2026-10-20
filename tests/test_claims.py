"""Tests for the work-claims system in cloud_dog_registry.py.

Claims are the Tekken 5 fix: a lane claims a path (owner/action/claim_id/
epoch/TTL+heartbeat) in the sqlite registry, and reapers must consult
claim_gate() before deleting. A live claim is a hard block; an unreadable
claims store fails CLOSED (no reap).

Every test works on tmp_path DBs — never the live registry.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud_dog_registry as R


@pytest.fixture()
def paths(tmp_path):
    jp = str(tmp_path / "reg.json")
    dp = str(tmp_path / "reg.db")
    return jp, dp


def _backdate_heartbeat(dp, path, seconds_ago):
    """Rewrite a claim's heartbeat_at to `seconds_ago` in the past."""
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()
    conn = sqlite3.connect(dp)
    try:
        conn.execute("UPDATE claims SET heartbeat_at=? WHERE path=?", (stamp, path))
        conn.commit()
    finally:
        conn.close()
    return stamp


def test_claim_roundtrip_and_fields(paths):
    jp, dp = paths
    ok, claim = R.claim_path(
        dp,
        "/work/game/file.iso",
        "dl-lane",
        "download",
        note="resuming from archive.org",
        ttl_s=1800,
        json_path=jp,
    )
    assert ok
    assert claim["owner"] == "dl-lane"
    assert claim["action"] == "download"
    assert claim["ttl_s"] == 1800
    assert claim["epoch"] == 1
    assert claim["claim_id"]
    claimed, found = R.is_claimed(dp, "/work/game/file.iso", json_path=jp)
    assert claimed and found["claim_id"] == claim["claim_id"]
    # journaled
    actions = [a for _, a, _, _ in R.journal_tail(dp, 10)]
    assert "claim" in actions


def test_claim_blocks_other_owner_but_not_same_owner(paths):
    jp, dp = paths
    ok, first = R.claim_path(dp, "/w/f", "lane-a", "download", json_path=jp)
    assert ok
    ok2, held = R.claim_path(dp, "/w/f", "lane-b", "reap", json_path=jp)
    assert not ok2
    assert held["owner"] == "lane-a"
    assert held["claim_id"] == first["claim_id"]
    # same owner re-claims: fresh claim_id, epoch fencing increments
    ok3, second = R.claim_path(dp, "/w/f", "lane-a", "download", json_path=jp)
    assert ok3
    assert second["claim_id"] != first["claim_id"]
    assert second["epoch"] == first["epoch"] + 1


def test_ancestor_claim_covers_children(paths):
    jp, dp = paths
    ok, _ = R.claim_path(dp, "/work/game-reverse-engineering", "teardown", "extract", json_path=jp)
    assert ok
    claimed, claim = R.is_claimed(
        dp, "/work/game-reverse-engineering/tekken5/file.iso", json_path=jp
    )
    assert claimed and claim["owner"] == "teardown"
    claimed2, _ = R.is_claimed(dp, "/work/other/file.iso", json_path=jp)
    assert not claimed2
    # prefix must respect the separator: /work/game2 is NOT under /work/game
    ok3, _ = R.claim_path(dp, "/work/game", "lane", "download", json_path=jp)
    assert ok3
    claimed3, _ = R.is_claimed(dp, "/work/game2/file", json_path=jp)
    assert not claimed3


def test_expired_claim_does_not_block_and_can_be_retaken(paths):
    jp, dp = paths
    ok, claim = R.claim_path(dp, "/w/f", "lane-a", "download", ttl_s=60, json_path=jp)
    assert ok
    _backdate_heartbeat(dp, "/w/f", 3600)  # heartbeat 1h old, ttl 60s
    claimed, _ = R.is_claimed(dp, "/w/f", json_path=jp)
    assert not claimed
    allowed, _ = R.claim_gate(dp, "/w/f", json_path=jp)
    assert allowed
    ok2, second = R.claim_path(dp, "/w/f", "lane-b", "download", json_path=jp)
    assert ok2 and second["owner"] == "lane-b"
    assert second["epoch"] == claim["epoch"] + 1


def test_heartbeat_renews_and_stale_holder_is_fenced(paths):
    jp, dp = paths
    ok, first = R.claim_path(dp, "/w/f", "lane-a", "download", ttl_s=60, json_path=jp)
    assert ok
    _backdate_heartbeat(dp, "/w/f", 3600)  # lease lapsed
    ok2, second = R.claim_path(dp, "/w/f", "lane-b", "download", ttl_s=60, json_path=jp)
    assert ok2
    # stale holder's heartbeat must NOT revive/steal lane-b's claim
    assert not R.heartbeat_claim(dp, "/w/f", first["claim_id"], json_path=jp)
    # current holder's heartbeat renews
    _backdate_heartbeat(dp, "/w/f", 50)  # 50s into a 60s ttl: still live
    assert R.heartbeat_claim(dp, "/w/f", second["claim_id"], json_path=jp)
    claimed, found = R.is_claimed(dp, "/w/f", json_path=jp)
    assert claimed and found["claim_id"] == second["claim_id"]


def test_release_is_claim_id_guarded_and_epoch_survives(paths):
    jp, dp = paths
    ok, first = R.claim_path(dp, "/w/f", "lane-a", "download", json_path=jp)
    assert ok
    # wrong claim_id releases nothing
    assert not R.release_claim(dp, "/w/f", "deadbeef", json_path=jp)
    claimed, _ = R.is_claimed(dp, "/w/f", json_path=jp)
    assert claimed
    # right claim_id releases; idempotent second release returns False
    assert R.release_claim(dp, "/w/f", first["claim_id"], json_path=jp)
    assert not R.release_claim(dp, "/w/f", first["claim_id"], json_path=jp)
    claimed, _ = R.is_claimed(dp, "/w/f", json_path=jp)
    assert not claimed
    # epoch is monotonic per path even across a release
    ok2, second = R.claim_path(dp, "/w/f", "lane-a", "download", json_path=jp)
    assert ok2 and second["epoch"] == first["epoch"] + 1


def test_expire_claims_sweeps_only_lapsed(paths):
    jp, dp = paths
    R.claim_path(dp, "/w/live", "lane-a", "download", ttl_s=600, json_path=jp)
    R.claim_path(dp, "/w/dead", "lane-b", "render", ttl_s=60, json_path=jp)
    _backdate_heartbeat(dp, "/w/dead", 3600)
    n = R.expire_claims(dp, json_path=jp)
    assert n == 1
    claims = R.list_claims(dp, json_path=jp)
    assert [c["path"] for c in claims] == ["/w/live"]
    actions = [a for _, a, _, _ in R.journal_tail(dp, 10)]
    assert "claim-expire" in actions


def test_claim_gate_blocks_live_claim(paths):
    jp, dp = paths
    allowed, reason = R.claim_gate(dp, "/w/f", json_path=jp)
    assert allowed and reason == ""
    R.claim_path(dp, "/w/f", "t5-iso-dl", "download", json_path=jp)
    allowed, reason = R.claim_gate(dp, "/w/f", json_path=jp)
    assert not allowed
    assert "t5-iso-dl" in reason


def test_claim_gate_fails_closed_on_corrupt_db(tmp_path):
    dp = str(tmp_path / "corrupt.db")
    with open(dp, "wb") as f:
        f.write(b"this is not a sqlite database at all" * 64)
    allowed, reason = R.claim_gate(dp, "/w/f")
    assert not allowed  # unreadable claims => NO reap, ever
    assert "failing closed" in reason


def test_claims_live_in_sqlite_even_for_json_backend_registry(paths):
    jp, dp = paths
    reg = R.Registry(jp, dp, backend="json")
    ok, claim = reg.claim("/w/f", "lane-a", "download")
    assert ok
    # a sqlite-backend handle sees the same claim (single source of truth)
    reg2 = R.Registry(jp, dp, backend="sqlite")
    found = reg2.get_claim("/w/f")
    assert found and found["claim_id"] == claim["claim_id"]
    assert reg2.release_claim("/w/f", claim["claim_id"])
