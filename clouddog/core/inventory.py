"""Inventory scan: find large sweep candidates fast, never raising on bad dirs."""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Candidate kinds.
KIND_GIT_CLONE = "git-clone"
KIND_VENV = "venv"
KIND_BULK_DIR = "bulk-dir"
KIND_MEDIA_FILE = "media-file"
KIND_UNKNOWN = "unknown"

_MEDIA_SUFFIXES = {".mp4", ".mkv", ".mov", ".iso"}


@dataclass
class Candidate:
    """A large file or directory that may be worth storing and clearing."""

    path: str
    size_bytes: int
    mtime: float
    kind: str  # one of git-clone, venv, bulk-dir, media-file, unknown
    note: str = ""


def _dir_size(path: str) -> int:
    """Sum file sizes under *path*, skipping symlinked dirs, tolerating errors."""
    total = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                total += _dir_size(entry.path)
            else:
                total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def _classify(path: str, is_dir: bool) -> str:
    """Classify a path by simple heuristics."""
    name = os.path.basename(path).lower()
    if is_dir:
        if os.path.isdir(os.path.join(path, ".git")):
            return KIND_GIT_CLONE
        if "venv" in name or os.path.isfile(os.path.join(path, "pyvenv.cfg")):
            return KIND_VENV
        return KIND_BULK_DIR
    suffix = os.path.splitext(name)[1]
    if suffix in _MEDIA_SUFFIXES:
        return KIND_MEDIA_FILE
    return KIND_UNKNOWN


def scan(root: str, max_depth: int = 2, min_bytes: int = 50 * 1024 * 1024) -> list[Candidate]:
    """Scan *root* at most *max_depth* levels deep for large candidates.

    Directories get their full subtree size; symlinked dirs are skipped;
    permission errors are tolerated. Returns candidates at or above
    *min_bytes*, biggest first. Never raises on unreadable directories.
    """
    found: list[Candidate] = []
    try:
        levels: list[list[str]] = [[root]]
    except OSError:
        return []
    for depth in range(max_depth + 1):
        if depth >= len(levels):
            break
        next_level: list[str] = []
        for parent in levels[depth]:
            try:
                children = list(os.scandir(parent))
            except OSError:
                continue
            for child in children:
                try:
                    if child.is_symlink():
                        continue
                    is_dir = child.is_dir(follow_symlinks=False)
                    st = child.stat(follow_symlinks=False)
                except OSError:
                    continue
                size = _dir_size(child.path) if is_dir else st.st_size
                if size >= min_bytes:
                    found.append(
                        Candidate(
                            path=child.path,
                            size_bytes=size,
                            mtime=st.st_mtime,
                            kind=_classify(child.path, is_dir),
                        )
                    )
                if is_dir and depth < max_depth:
                    next_level.append(child.path)
        levels.append(next_level)
    found.sort(key=lambda c: c.size_bytes, reverse=True)
    return found
