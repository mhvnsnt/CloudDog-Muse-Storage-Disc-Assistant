"""Disk usage monitor: read how full a path is."""

from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class DiskState:
    """Snapshot of disk usage for one path."""

    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    pct_used: float


def disk_usage(path: str = "/home/hatch") -> DiskState:
    """Return a :class:`DiskState` for the filesystem containing *path*."""
    usage = shutil.disk_usage(path)
    pct = (usage.used / usage.total * 100.0) if usage.total else 0.0
    return DiskState(
        path=path,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        pct_used=pct,
    )
