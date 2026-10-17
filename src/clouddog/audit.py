"""Append-only audit journal for CloudDog disk operations.

Every store / verify / reap / clear action is recorded as one JSONL line
(machine-readable, fsync'd) and, optionally, as a human-readable markdown
bullet matching the long-standing format of
~/workspace/agent-ops/disk-cleanup-log.md.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AuditEvent:
    ts: str = field(default_factory=_utcnow_iso)  # UTC ISO
    kind: str = "sweep"  # stored | verified | reaped | cleared | skipped | error | sweep
    path: str = ""
    size: int | None = None
    locator: str | None = None  # where it lives now (repo@tag/asset, /terabox/path, ...)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "kind": self.kind,
            "path": self.path,
            "size": self.size,
            "locator": self.locator,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEvent":
        return cls(
            ts=d.get("ts", _utcnow_iso()),
            kind=d.get("kind", "sweep"),
            path=d.get("path", ""),
            size=d.get("size"),
            locator=d.get("locator"),
            note=d.get("note", ""),
        )


_KIND_ACTION = {
    "stored": "Stored",
    "verified": "Verified",
    "reaped": "Reaped",
    "cleared": "Cleared",
    "skipped": "Skipped",
    "error": "Error",
    "sweep": "Sweep",
}

# Kinds that free disk space and count toward "Total freed".
_FREED_KINDS = ("cleared", "reaped")


def fmt_size(n: int | None) -> str:
    """Human size like the disk-cleanup-log format (~1.2G, 986M, 35K)."""
    if n is None or n < 0:
        return "unknown size"
    v = float(n)
    for unit in ("B", "K", "M", "G", "T"):
        if v < 1024 or unit == "T":
            break
        v /= 1024
    if unit == "B":
        return "%dB" % n
    s = ("%.1f" % v).rstrip("0").rstrip(".")
    return s + unit


class AuditJournal:
    """Append-only JSONL journal with optional rendered markdown copy."""

    def __init__(self, jsonl_path: str, md_path: str | None = None,
                 md_title: str = "CloudDog audit journal") -> None:
        self._jsonl_path = jsonl_path
        self._md_path = md_path
        self._md_title = md_title

    def _append_file(self, path: str, text: str) -> None:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "a") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

    def record(self, event: AuditEvent) -> None:
        """Append one event: JSONL always, markdown bullet when md_path is set."""
        self._append_file(self._jsonl_path, json.dumps(event.to_dict()) + "\n")
        if self._md_path:
            if not os.path.exists(self._md_path) or os.path.getsize(self._md_path) == 0:
                date = event.ts.split("T")[0]
                self._append_file(
                    self._md_path, "## %s — %s\n\n" % (date, self._md_title)
                )
            for line in self._render_bullets([event]):
                self._append_file(self._md_path, line + "\n")

    def read(self, limit: int = 100) -> list[dict]:
        """Return the last `limit` journal entries (oldest first)."""
        if limit <= 0:
            return []
        try:
            with open(self._jsonl_path) as f:
                lines = f.read().splitlines()
        except FileNotFoundError:
            return []
        out = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    @staticmethod
    def _render_bullets(events: list[AuditEvent]) -> list[str]:
        lines = []
        for e in events:
            action = _KIND_ACTION.get(e.kind, e.kind.capitalize() or "Event")
            line = "- %s `%s` (%s, %s)" % (
                action,
                e.path,
                fmt_size(e.size),
                e.locator or "local only",
            )
            if e.note:
                line += " — %s" % e.note
            lines.append(line)
        return lines

    @staticmethod
    def render_markdown(events: list[AuditEvent], title: str) -> str:
        """Render a full human-readable section in disk-cleanup-log style:

        ## <date> — <title>
        - <action> `<path>` (<size>, <locator>) — <note>
        **Total freed:** <size>
        """
        date = events[0].ts.split("T")[0] if events else time.strftime("%Y-%m-%d")
        parts = ["## %s — %s" % (date, title), ""]
        parts.extend(AuditJournal._render_bullets(events))
        freed = sum(
            e.size for e in events
            if e.kind in _FREED_KINDS and e.size is not None and e.size >= 0
        )
        if any(e.kind in _FREED_KINDS and e.size is not None for e in events):
            parts += ["", "**Total freed:** %s" % fmt_size(freed)]
        return "\n".join(parts) + "\n"
