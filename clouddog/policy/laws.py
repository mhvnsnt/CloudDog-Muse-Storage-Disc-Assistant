"""The owner's disk laws, encoded as code.

Law sources (owner's verbatim rules, ~/AGENTS.md):
- NEVER-DELETE-GAME-FILES LAW (2026-10-14): "GAME FILES ARE NEVER AUTO-DELETED."
  Covers ISOs, ROMs, game archives (.zip/.rar/.7z of full games), teardown
  source files, anything in game-reverse-engineering/, chimera-roms/, or any
  teardown lane's source material. REFINED 2026-10-17 (not deleted): owner
  verbatim — "it can deal with game files, only if it's a hundred percent
  saved into the repos... it has to be a hundred percent stored somewhere
  where it's savable and where we can get back to it, that's really the
  biggest deal." The blanket ban is now the strictest bar in the system:
  deletion allowed ONLY at 100% byte-verified store (hash+size) +
  retrievability proven (pull-back actually tested; a listing is NOT proof)
  + owner surfaced (never silent).
- NEVER-KILL-IN-PROGRESS LAW (2026-10-15) / NO-SWEEP-INFLIGHT LAW (2026-10-16):
  "NEVER delete, move, or 'clean up' anything that is downloading, uploading,
  rendering, extracting, or otherwise in-flight -- no matter how full the disk
  gets." "NO override exists" for in-progress paths.
- ONE-STORE RULE (2026-10-16, supersedes the both-stores relaxation): "verify
  that stuff is over To GitHub or to Terrabox it does not have to be to both
  it just has to be safely secured to one or the other 100% then it can be
  cleared."
- NO-DELETE-BEFORE-SAFE-STORAGE LAW (2026-10-13): "NEVER delete anything
  currently in use for disk space before it is VERIFIED safe on GitHub,
  TeraBox, or another permanent store. No exceptions."
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field

#: Suffixes that mark a file as a protected game file anywhere on disk.
PROTECTED_SUFFIXES = {
    ".iso",
    ".img",
    ".bin",
    ".cue",
    ".rom",
    ".n64",
    ".z64",
    ".sfc",
    ".smc",
    ".gba",
    ".gbc",
    ".nds",
    ".3ds",
    ".chd",
    ".cso",
    ".pkg",
    ".rvz",
    ".xci",
    ".nsp",
    ".7z",
    ".rar",
}

#: Suffixes that are only "protected" as game archives when the path also
#: sits under a protected directory (per the law: archives of full games).
ARCHIVE_SUFFIXES = {".7z", ".rar"}

#: Directory names that make everything beneath them protected.
PROTECTED_PATH_PARTS = {
    "game-reverse-engineering",
    "chimera-roms",
    "teardown",
    "isos",
    "roms",
    "game-archives",
}

#: Suffixes that mark a file as actively downloading/in-flight.
IN_PROGRESS_SUFFIXES = {
    ".part",
    ".aria2",
    ".crdownload",
    ".kaggle-partial",
    ".tmp",
    ".download",
    ".partial",
}

#: Directory name hints that mark a tree as in-flight.
IN_PROGRESS_DIR_HINTS = {"staging", "downloads", "incomplete", "tmp"}

#: A file whose name starts with this, found in any ancestor directory of a
#: path, protects that whole tree from deletion.
DO_NOT_DELETE_MARKER = ".DO_NOT_DELETE"


@dataclass
class Decision:
    """The policy verdict for deleting one path."""

    allowed: bool
    requires_owner: bool
    reasons: list[str] = field(default_factory=list)


def _parts(path: str) -> list[str]:
    """Return the lowercased path components of *path*."""
    normalized = os.path.normpath(os.path.expanduser(path))
    return [p.lower() for p in normalized.split(os.sep)]


def _extra_match(path: str, extra_patterns: tuple) -> str | None:
    """Return the first extra pattern matching *path*, or None."""
    lowered = os.path.expanduser(path).lower()
    for pattern in extra_patterns:
        pat = str(pattern).lower()
        if fnmatch.fnmatch(lowered, pat) or any(
            fnmatch.fnmatch(part, pat) for part in _parts(path)
        ):
            return str(pattern)
    return None


def is_protected_game_file(path: str, extra_patterns: tuple = ()) -> tuple[bool, str]:
    """Check whether *path* is a protected game file.

    Protected means: any directory in the path matches
    :data:`PROTECTED_PATH_PARTS` (or *extra_patterns*), or the file suffix is
    in :data:`PROTECTED_SUFFIXES`. Archive suffixes (.7z/.rar) only count as
    game archives when the path also matches a protected directory.
    Returns ``(protected, reason)``.
    """
    parts = _parts(path)
    dir_hit = next((p for p in parts if p in PROTECTED_PATH_PARTS), None)
    extra_hit = _extra_match(path, extra_patterns)
    suffix = os.path.splitext(os.path.basename(path))[1].lower()

    if dir_hit is not None or extra_hit is not None:
        where = f"directory '{dir_hit}'" if dir_hit else f"pattern '{extra_hit}'"
        return True, f"protected path ({where})"
    if suffix in ARCHIVE_SUFFIXES:
        return False, "archive suffix but not under a protected directory"
    if suffix in PROTECTED_SUFFIXES:
        return True, f"protected game-file suffix '{suffix}'"
    return False, "not a protected game file"


def has_do_not_delete_marker(path: str) -> bool:
    """Walk up the ancestors of *path*; True if any holds a marker file."""
    current = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(current):
        current = os.path.dirname(current)
    while True:
        try:
            names = os.listdir(current)
        except OSError:
            names = []
        if any(n.startswith(DO_NOT_DELETE_MARKER) for n in names):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def is_in_progress(path: str) -> tuple[bool, str]:
    """Check whether *path* is in-flight (downloading/uploading/rendering)."""
    suffix = os.path.splitext(os.path.basename(path))[1].lower()
    if suffix in IN_PROGRESS_SUFFIXES:
        return True, f"in-progress suffix '{suffix}'"
    hint = next((p for p in _parts(path) if p in IN_PROGRESS_DIR_HINTS), None)
    if hint is not None:
        return True, f"in-progress directory hint '{hint}'"
    return False, "not in progress"


def decide_delete(
    path: str,
    *,
    verified_stores: list[str],
    owner_approved: bool = False,
    retrievability_proven: bool = False,
    owner_surfaced: bool = False,
    extra_patterns: tuple = (),
) -> Decision:
    """Decide whether deleting *path* is allowed under the owner's laws.

    Rules, in order:
    1. In-progress -> never allowed (NEVER-KILL-IN-PROGRESS / NO-SWEEP-INFLIGHT;
       no override exists, not even the owner's).
    2. .DO_NOT_DELETE marker in ancestry -> not allowed; owner must handle it.
    3. Protected game file -> GAME-FILE STRICT BAR (owner 2026-10-17,
       refines the 2026-10-14 blanket ban): "it can deal with game files,
       only if it's a hundred percent saved into the repos... it has to be
       a hundred percent stored somewhere where it's savable and where we
       can get back to it, that's really the biggest deal." Allowed ONLY
       when ALL three hold: (a) verified_stores non-empty — callers pass
       ONLY fully 100% byte-verified (hash+size) stores; (b)
       retrievability_proven=True — pull-back actually tested (asset
       downloadable / manifest fetched; a listing is NOT proof);
       (c) owner_surfaced=True — the store event was surfaced to the owner,
       never silent (owner_approved=True satisfies this for backward
       compat). Any condition missing -> not allowed, requires_owner=True,
       and the reasons name exactly which condition is missing.
    4. Ordinary file verified 100% on at least one store -> allowed
       (ONE-STORE RULE; no per-file ask).
    5. Nothing verified on any store -> not allowed
       (NO-DELETE-BEFORE-SAFE-STORAGE).
    """
    in_progress, why = is_in_progress(path)
    if in_progress:
        return Decision(
            allowed=False,
            requires_owner=False,
            reasons=[
                (
                    f"in-progress: {why} "
                    "(NEVER-KILL-IN-PROGRESS / NO-SWEEP-INFLIGHT: no override exists)"
                ),
            ],
        )

    if has_do_not_delete_marker(path):
        return Decision(
            allowed=False,
            requires_owner=True,
            reasons=[
                (
                    f"'{DO_NOT_DELETE_MARKER}' marker found in ancestry of '{path}'; "
                    "tree is protected until the marker is removed"
                ),
            ],
        )

    protected, why = is_protected_game_file(path, extra_patterns)
    if protected:
        stores = list(verified_stores)
        # owner_approved implies the owner saw (and approved) this deletion,
        # so it satisfies owner_surfaced for backward compatibility.
        surfaced = bool(owner_surfaced or owner_approved)
        missing = []
        if not stores:
            missing.append(
                "verified_stores: no 100% byte-verified store (hash+size) on GitHub OR TeraBox"
            )
        if not retrievability_proven:
            missing.append(
                "retrievability_proven: pull-back must be actually tested "
                "(asset downloadable / manifest fetched) — a listing is NOT proof"
            )
        if not surfaced:
            missing.append(
                "owner_surfaced: the store event must be surfaced to the owner, "
                "never silent (owner_approved also satisfies this)"
            )
        if missing:
            return Decision(
                allowed=False,
                requires_owner=True,
                reasons=[
                    (
                        f"protected game file ({why}); GAME-FILE STRICT BAR "
                        f"(owner 2026-10-17): missing {', '.join(missing)}; "
                        "ONE-STORE RULE requires 100% verification on GitHub OR TeraBox"
                    ),
                ],
            )
        return Decision(
            allowed=True,
            requires_owner=True,
            reasons=[
                (f"protected game file ({why}); GAME-FILE STRICT BAR (owner 2026-10-17) satisfied"),
                f"verified_stores: 100% byte-verified on {', '.join(stores)} (hash+size)",
                "retrievability_proven: pull-back actually tested",
                "owner_surfaced: store event surfaced to the owner",
            ],
        )

    if verified_stores:
        return Decision(
            allowed=True,
            requires_owner=False,
            reasons=[
                (
                    f"ONE-STORE RULE: verified 100% on {', '.join(verified_stores)}; "
                    "no per-file ask required"
                ),
            ],
        )
    return Decision(
        allowed=False,
        requires_owner=False,
        reasons=[
            (
                "NO-DELETE-BEFORE-SAFE-STORAGE: not verified on any store; "
                "upload first, verify, then delete"
            ),
        ],
    )
