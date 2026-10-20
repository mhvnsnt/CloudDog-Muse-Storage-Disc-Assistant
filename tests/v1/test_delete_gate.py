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
    kw = dict(
        registry_entry=_entry(),
        local_sha256=SHA,
        local_size=SIZE,
        live_verify_ok=True,
        claim_check=_free,
        policy_action="store_and_clear",
    )
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
    v = _verdict(registry_entry=entry, local_sha256="", local_digest=digest, local_digest_algo="git-sha1")
    assert v.allowed is True
    v2 = _verdict(registry_entry=entry, local_sha256="", local_digest="0" * 40, local_digest_algo="git-sha1")
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
    v = _verdict(registry_entry=None, live_verify_ok=False, claim_check=_claimed, policy_action="sacred")
    assert v.allowed is False
    assert _legs(v) == {"registry", "verify", "claims", "policy"}
