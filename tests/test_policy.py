"""Tests for clouddog.policy — the disk-law policy engine.

Uses tmp_path and sparse files (os.truncate) so size-boundary tests are
instant. No network, no secrets, no mock-data nonsense in the module.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from clouddog.policy import (
    Action,
    Decision,
    FIVE_HUNDRED_MB,
    TEN_MB,
    evaluate,
    scan,
    suggest_tier,
)


def make_file(path: Path, size: int, mtime: float | None = None) -> Path:
    """Create a sparse file of `size` bytes, optionally backdated."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb"):
        pass
    os.truncate(path, size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


OLD = time.time() - 7200  # 2h ago: cold, not hot, not recently modified


# ---------------------------------------------------------------- sacred ---
def test_sacred_part_suffix(tmp_path: Path):
    f = make_file(tmp_path / "movie.mkv.part", 100, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.SACRED
    assert d.tier is None


def test_sacred_aria2_suffix(tmp_path: Path):
    f = make_file(tmp_path / "archive.zip.aria2", 100, mtime=OLD)
    assert evaluate(str(f)).action is Action.SACRED


def test_sacred_kaggle_partial_suffix(tmp_path: Path):
    f = make_file(tmp_path / "chunk.kaggle-partial", 100, mtime=OLD)
    assert evaluate(str(f)).action is Action.SACRED


def test_sacred_do_not_delete_marker_in_parent(tmp_path: Path):
    (tmp_path / ".DO_NOT_DELETE").write_text("hands off")
    child = make_file(tmp_path / "big.bin", 20 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(child))
    assert d.action is Action.SACRED
    assert ".DO_NOT_DELETE" in d.reason


def test_sacred_do_not_delete_marker_grandparent(tmp_path: Path):
    (tmp_path / ".DO_NOT_DELETE-keep").write_text("hands off")
    child = make_file(tmp_path / "sub" / "deep" / "file.dat", 100, mtime=OLD)
    assert evaluate(str(child)).action is Action.SACRED


def test_sacred_recently_modified_large_file(tmp_path: Path):
    # 2MB, mtime = now -> possibly in-flight
    f = make_file(tmp_path / "render.mp4", 2 * 1024 * 1024)
    d = evaluate(str(f))
    assert d.action is Action.SACRED
    assert "recently modified, possibly in-flight" in d.reason


def test_not_sacred_when_old_and_small(tmp_path: Path):
    f = make_file(tmp_path / "note.txt", 100, mtime=OLD)
    assert evaluate(str(f)).action is not Action.SACRED


# ----------------------------------------------------------- never_delete ---
def test_never_delete_iso_under_teardown_dir(tmp_path: Path):
    f = make_file(tmp_path / "teardown" / "tekken3" / "disc.iso", 600 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.NEVER_DELETE
    assert "NEVER-DELETE-GAME-FILES" in d.reason


def test_never_delete_bare_iso(tmp_path: Path):
    # .iso qualifies on extension alone, no path hint needed
    f = make_file(tmp_path / "random-folder" / "game.iso", 600 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.NEVER_DELETE
    assert "NEVER-DELETE-GAME-FILES" in d.reason


def test_never_delete_rom_extension_in_rom_dir(tmp_path: Path):
    f = make_file(tmp_path / "rom" / "no-mercy.z64", 100, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.NEVER_DELETE


def test_bin_needs_game_component(tmp_path: Path):
    # .bin alone, no game component -> NOT a game file under the rules
    f = make_file(tmp_path / "plain" / "data.bin", 20 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.STORE_AND_CLEAR

    f2 = make_file(tmp_path / "chimera" / "data.bin", 20 * 1024 * 1024, mtime=OLD)
    d2 = evaluate(str(f2))
    assert d2.action is Action.NEVER_DELETE


def test_never_delete_may_still_store(tmp_path: Path):
    f = make_file(tmp_path / "teardown" / "x.chd", 600 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.NEVER_DELETE
    assert d.tier == "terabox"  # may store, never auto-delete


# ------------------------------------------------------------- keep_local ---
def test_keep_local_tools_component(tmp_path: Path):
    f = make_file(tmp_path / "tools" / "blender" / "big.dat", 50 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.KEEP_LOCAL
    assert d.tier is None


def test_keep_local_git_component(tmp_path: Path):
    f = make_file(tmp_path / ".git" / "objects" / "pack.dat", 50 * 1024 * 1024, mtime=OLD)
    assert evaluate(str(f)).action is Action.KEEP_LOCAL


def test_keep_local_hot_small_file(tmp_path: Path):
    f = make_file(tmp_path / "active" / "notes.txt", 1024)  # fresh mtime, < 10MB
    d = evaluate(str(f))
    assert d.action is Action.KEEP_LOCAL


# ------------------------------------------------------------------- tiers ---
def test_tier_terabox_at_500mb_boundary(tmp_path: Path):
    big = make_file(tmp_path / "big.tar", FIVE_HUNDRED_MB, mtime=OLD)
    d = evaluate(str(big))
    assert d.action is Action.STORE_AND_CLEAR
    assert d.tier == "terabox"

    just_under = make_file(tmp_path / "under.tar", FIVE_HUNDRED_MB - 1, mtime=OLD)
    d2 = evaluate(str(just_under))
    assert d2.tier == "github"


def test_tier_terabox_for_media_extensions(tmp_path: Path):
    f = make_file(tmp_path / "clip.mp4", 50 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.tier == "terabox"

    f2 = make_file(tmp_path / "song.flac", 5 * 1024 * 1024, mtime=OLD)
    assert evaluate(str(f2)).tier == "terabox"


def test_tier_github_default(tmp_path: Path):
    f = make_file(tmp_path / "docs.zip", 50 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.tier == "github"


def test_suggest_tier_unit():
    assert suggest_tier(Path("x.tar"), FIVE_HUNDRED_MB) == "terabox"
    assert suggest_tier(Path("x.tar"), FIVE_HUNDRED_MB - 1) == "github"
    assert suggest_tier(Path("x.MKV"), 100) == "terabox"  # case-insensitive
    assert suggest_tier(Path("x.zip"), 100) == "github"


# ---------------------------------------------------------------- defaults ---
def test_default_store_and_clear_at_10mb(tmp_path: Path):
    f = make_file(tmp_path / "cold.tar", TEN_MB, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.STORE_AND_CLEAR
    assert d.tier == "github"

    f2 = make_file(tmp_path / "small.tar", TEN_MB - 1, mtime=OLD)
    d2 = evaluate(str(f2))
    assert d2.action is Action.STORE


def test_default_store_small_cold(tmp_path: Path):
    f = make_file(tmp_path / "archive.zip", 5 * 1024 * 1024, mtime=OLD)
    d = evaluate(str(f))
    assert d.action is Action.STORE
    assert d.tier == "github"


# ------------------------------------------------------------------- scan ---
def test_scan_sorted_desc_and_evaluated(tmp_path: Path):
    make_file(tmp_path / "small.dat", 1024, mtime=OLD)
    make_file(tmp_path / "big.tar", 20 * 1024 * 1024, mtime=OLD)
    sub = tmp_path / "subdir"
    make_file(sub / "a.dat", 5 * 1024 * 1024, mtime=OLD)
    make_file(sub / "b.dat", 5 * 1024 * 1024, mtime=OLD)

    rows = scan(str(tmp_path))
    assert len(rows) == 3
    sizes = [r["bytes"] for r in rows]
    assert sizes == sorted(sizes, reverse=True)
    assert rows[0]["bytes"] == 20 * 1024 * 1024
    assert rows[1]["bytes"] == 10 * 1024 * 1024  # dir summed via du
    assert rows[2]["bytes"] == 1024
    for r in rows:
        assert set(r) == {"path", "bytes", "action", "tier", "reason"}
    assert rows[0]["action"] == Action.STORE_AND_CLEAR.value
    assert rows[2]["action"] == Action.STORE.value


def test_scan_caps_entries(tmp_path: Path):
    for i in range(10):
        make_file(tmp_path / f"f{i}.dat", 100, mtime=OLD)
    rows = scan(str(tmp_path), max_entries=4)
    assert len(rows) == 4


def test_scan_nonexistent_root():
    rows = scan("/definitely/not/a/real/dir-xyz")
    assert len(rows) == 1
    assert rows[0]["action"] == Action.KEEP_LOCAL.value
    assert "skipped" in rows[0]["reason"]


def test_scan_does_not_follow_symlinks(tmp_path: Path):
    outside = make_file(tmp_path / "outside" / "huge.dat", 50 * 1024 * 1024, mtime=OLD)
    link = tmp_path / "link"
    link.symlink_to(outside)
    rows = scan(str(tmp_path))
    by_path = {r["path"]: r for r in rows}
    assert by_path[str(link)]["bytes"] == 0  # symlink not followed


# -------------------------------------------------------------- robustness ---
def test_evaluate_nonexistent_path_never_crashes():
    d = evaluate("/no/such/path/anywhere-xyz/file.dat")
    assert isinstance(d, Decision)
    assert d.action is Action.KEEP_LOCAL
    assert d.reason  # non-empty reason explaining why


def test_evaluate_empty_string():
    d = evaluate("")
    assert d.action is Action.KEEP_LOCAL


def test_decision_dataclass_shape():
    d = Decision(action=Action.STORE, tier="github", reason="r")
    assert d.action.value == "store"
    assert d.tier == "github"
