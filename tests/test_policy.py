"""Tests for clouddog.policy.laws and clouddog.policy.approvals (sibling-owned)."""

import pytest

from clouddog.policy import approvals, laws


def test_iso_under_teardown_is_protected():
    protected, reason = laws.is_protected_game_file("/data/teardown/tekken3/game.iso")
    assert protected is True
    assert reason


def test_extra_patterns_protect():
    protected, _ = laws.is_protected_game_file("/data/stuff/archive.bin", extra_patterns=("*.bin",))
    assert protected is True


def test_ordinary_file_not_protected(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"hi")
    protected, _ = laws.is_protected_game_file(str(target))
    assert protected is False


def test_in_progress_part_blocks_delete_even_with_owner_and_stores(tmp_path):
    target = tmp_path / "movie.mkv.part"
    target.write_bytes(b"x")
    assert laws.is_in_progress(str(target))[0] is True
    decision = laws.decide_delete(str(target), verified_stores=["terabox"], owner_approved=True)
    assert decision.allowed is False


def test_ordinary_file_with_one_verified_store_allowed(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"hi")
    decision = laws.decide_delete(str(target), verified_stores=["github"])
    assert decision.allowed is True
    assert decision.requires_owner is False


def test_no_verified_store_denied(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"hi")
    decision = laws.decide_delete(str(target), verified_stores=[])
    assert decision.allowed is False


def test_marker_on_ancestor_blocks_delete(tmp_path):
    (tmp_path / ".DO_NOT_DELETE").write_text("sacred", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "file.txt"
    target.write_bytes(b"hi")
    assert laws.has_do_not_delete_marker(str(target)) is True
    decision = laws.decide_delete(str(target), verified_stores=["terabox"], owner_approved=True)
    assert decision.allowed is False


def test_protected_game_file_needs_owner_and_store():
    path = "/x/teardown/game.iso"
    denied = laws.decide_delete(path, verified_stores=["terabox"], owner_approved=False)
    assert denied.allowed is False
    assert denied.requires_owner is True
    approved = laws.decide_delete(path, verified_stores=["terabox"], owner_approved=True)
    assert approved.allowed is True


def test_require_execute_raises_without_yes():
    with pytest.raises(approvals.ApprovalRequired):
        approvals.require_execute(dry_run=False, assume_yes=False, what="sweep")
    approvals.require_execute(dry_run=False, assume_yes=True, what="sweep")
    approvals.require_execute(dry_run=True, assume_yes=False, what="sweep")
