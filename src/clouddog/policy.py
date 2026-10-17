"""Policy engine for CloudDog: decide what to do with a path on disk.

Encodes the owner's disk laws verbatim-grounded, nothing invented:

- NEVER-KILL-IN-PROGRESS: anything in flight (partial-download suffixes,
  recently modified large files, paths under a ``.DO_NOT_DELETE*`` marker)
  is SACRED — do not touch it at all.
- NEVER-DELETE-GAME-FILES: game files (ISOs/ROMs/teardown sources) may be
  mirrored to a store but are never auto-deleted.
- Disk is a cache, not storage: big cold files are STORE_AND_CLEAR once
  byte-verified on a store; small hot files and tooling KEEP_LOCAL.

Stdlib only. No network, no secrets, never crashes on a bad path.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import time

__all__ = [
    "Action",
    "Decision",
    "evaluate",
    "scan",
    "suggest_tier",
    "MB",
    "TEN_MB",
    "FIVE_HUNDRED_MB",
    "ONE_HOUR",
    "FIVE_MINUTES",
]

MB = 1024 * 1024
TEN_MB = 10 * MB
FIVE_HUNDRED_MB = 500 * MB
ONE_HOUR = 3600
FIVE_MINUTES = 300

#: Partial-download / in-flight suffixes (NEVER-KILL-IN-PROGRESS).
IN_FLIGHT_SUFFIXES = frozenset({".part", ".aria2", ".kaggle-partial", ".tmp", ".downloading"})

#: Game-file extensions covered by NEVER-DELETE-GAME-FILES.
GAME_EXTENSIONS = frozenset(
    {
        ".iso", ".cso", ".rom", ".nes", ".sfc", ".smc", ".gba", ".gb",
        ".n64", ".z64", ".chd", ".mdf", ".nrg", ".bin", ".cue",
    }
)

#: Extensions that qualify as game files on their own, no path hint needed.
BARE_GAME_EXTENSIONS = frozenset({".iso", ".cso", ".rom", ".chd"})

#: Path components (case-insensitive) that mark game/teardown material.
GAME_COMPONENTS = frozenset({"game", "rom", "teardown", "chimera"})

#: Path components (case-insensitive) that mark tooling: always KEEP_LOCAL.
TOOL_COMPONENTS = frozenset({"tools", ".git"})

#: Media extensions that belong on TeraBox (bulk store).
MEDIA_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".mp3", ".flac", ".wav"})

TIER_TERABOX = "terabox"
TIER_GITHUB = "github"
TIER_DRIVE = "drive"


class Action(Enum):
    """What CloudDog should do with a path."""

    STORE = "store"  # mirror to tier; keep local for now
    STORE_AND_CLEAR = "store_and_clear"  # eligible to clear after byte-verified store
    KEEP_LOCAL = "keep_local"  # active work / small / tooling
    SACRED = "sacred"  # in-flight or protected: do not touch at all
    NEVER_DELETE = "never_delete"  # game files: may store, never auto-delete


@dataclass
class Decision:
    """Policy verdict for one path."""

    action: Action
    tier: str | None  # "github" | "terabox" | "drive" | None
    reason: str


def _stat_info(path: Path) -> tuple[int, float] | None:
    """Return (size_bytes, mtime) for a file or dir (dir = summed du).

    Never follows symlinks. Returns None when the path cannot be statted.
    """
    try:
        if path.is_symlink():
            target = path.resolve(strict=False)
            if not target.exists():
                return None
            path = target
        if path.is_dir():
            size = _du(path)
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            return size, mtime
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None


def _du(root: Path) -> int:
    """Sum file bytes under a directory. No symlink following; skips errors."""
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False, onerror=lambda _e: None):
            for name in filenames:
                fp = os.path.join(dirpath, name)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _has_do_not_delete_marker(path: Path, scan_root: Path | None) -> bool:
    """True if path itself (when a dir) or any parent dir up to scan_root
    contains a file whose name starts with ``.DO_NOT_DELETE``."""
    if path.is_dir():
        current = path
    else:
        current = path.parent
    stop = scan_root.resolve() if scan_root is not None else None
    while True:
        try:
            for child in current.iterdir():
                if child.name.startswith(".DO_NOT_DELETE"):
                    return True
        except OSError:
            pass
        if stop is not None and current.resolve() == stop:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


def _is_sacred(path: Path, scan_root: Path | None, size: int, mtime: float) -> str | None:
    """Return the sacred reason, or None if the path is not sacred.

    NEVER-KILL-IN-PROGRESS: in-flight work is never touched.
    """
    if path.suffix.lower() in IN_FLIGHT_SUFFIXES or path.name.lower().endswith(tuple(IN_FLIGHT_SUFFIXES)):
        return f"sacred: in-flight download suffix '{path.suffix}' (NEVER-KILL-IN-PROGRESS)"
    if _has_do_not_delete_marker(path, scan_root):
        return "sacred: .DO_NOT_DELETE marker in path or a parent dir (NEVER-KILL-IN-PROGRESS)"
    age = time.time() - mtime
    if age < FIVE_MINUTES and size > MB:
        return "sacred: recently modified, possibly in-flight (NEVER-KILL-IN-PROGRESS)"
    return None


def _is_game_file(path: Path) -> bool:
    """True when the path is a game file under NEVER-DELETE-GAME-FILES."""
    ext = path.suffix.lower()
    if ext not in GAME_EXTENSIONS:
        return False
    if ext in BARE_GAME_EXTENSIONS:
        return True
    components = {part.lower() for part in path.parts}
    return bool(components & GAME_COMPONENTS)


def suggest_tier(path: Path, size: int) -> str:
    """Pick a store tier: big or media blobs go to TeraBox, else GitHub."""
    if size >= FIVE_HUNDRED_MB:
        return TIER_TERABOX
    if path.suffix.lower() in MEDIA_EXTENSIONS:
        return TIER_TERABOX
    return TIER_GITHUB


def evaluate(path: str, scan_root: str | None = None) -> Decision:
    """Evaluate one path and return a Decision. Never raises on bad input.

    Check order mirrors the owner's laws: SACRED (in-flight/protected) wins
    over everything, then NEVER-DELETE-GAME-FILES, then KEEP_LOCAL (tooling /
    hot small files), then the size-based default.
    """
    if not path or not str(path).strip():
        return Decision(
            Action.KEEP_LOCAL,
            None,
            "keep_local: empty path; never act on what you cannot see",
        )
    p = Path(path)
    info = _stat_info(p)
    if info is None:
        return Decision(
            Action.KEEP_LOCAL,
            None,
            "keep_local: path does not exist or is unreadable; never act on what you cannot see",
        )
    size, mtime = info

    root = Path(scan_root) if scan_root is not None else None
    sacred_reason = _is_sacred(p, root, size, mtime)
    if sacred_reason is not None:
        return Decision(Action.SACRED, None, sacred_reason)

    if _is_game_file(p):
        return Decision(
            Action.NEVER_DELETE,
            suggest_tier(p, size),
            "never_delete: game file (NEVER-DELETE-GAME-FILES) — may mirror to store, never auto-delete",
        )

    components = {part.lower() for part in p.parts}
    if components & TOOL_COMPONENTS:
        return Decision(Action.KEEP_LOCAL, None, "keep_local: tooling path (tools/ or .git/)")
    if size < TEN_MB and (time.time() - mtime) < ONE_HOUR:
        return Decision(Action.KEEP_LOCAL, None, "keep_local: hot small file (under 10MB, modified within the hour)")

    tier = suggest_tier(p, size)
    if size >= TEN_MB:
        return Decision(
            Action.STORE_AND_CLEAR,
            tier,
            f"store_and_clear: cold file >= 10MB — mirror to {tier} and clear local only after byte-verified store",
        )
    return Decision(
        Action.STORE,
        tier,
        f"store: mirror to {tier}; keep local for now",
    )


def scan(root: str, max_entries: int = 200) -> list[dict]:
    """Scan top-level entries of root: du each, evaluate, sort bytes desc.

    Never follows symlinks, never blows up on permission errors (unreadable
    entries are kept with a skip reason). Returns at most max_entries dicts
    of {path, bytes, action, tier, reason}.
    """
    root_path = Path(root)
    entries: list[dict] = []
    try:
        children = list(root_path.iterdir())
    except OSError as exc:
        return [{"path": str(root_path), "bytes": 0, "action": Action.KEEP_LOCAL.value,
                 "tier": None, "reason": f"skipped: cannot list directory ({exc})"}]

    for child in children:
        try:
            if child.is_symlink():
                size = 0
            elif child.is_dir():
                size = _du(child)
            else:
                size = child.stat().st_size
        except OSError as exc:
            entries.append({
                "path": str(child),
                "bytes": 0,
                "action": Action.KEEP_LOCAL.value,
                "tier": None,
                "reason": f"skipped: unreadable ({exc})",
            })
            continue
        decision = evaluate(str(child), scan_root=str(root_path))
        entries.append({
            "path": str(child),
            "bytes": size,
            "action": decision.action.value,
            "tier": decision.tier,
            "reason": decision.reason,
        })

    entries.sort(key=lambda e: e["bytes"], reverse=True)
    return entries[:max_entries]
