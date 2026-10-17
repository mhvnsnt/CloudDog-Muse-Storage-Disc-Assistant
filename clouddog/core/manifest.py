"""Append-only audit journal: every delete ever gets an entry.

The journal is JSONL, one object per line, fsync'd on every append so an
entry is never silently lost if the process dies mid-sweep.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field


@dataclass
class JournalEntry:
    """One audited event: a delete, store, verify, or sweep plan."""

    ts: str  # UTC ISO timestamp
    action: str  # "delete" | "store" | "verify" | "sweep-plan"
    path: str
    size_bytes: int
    sha256: str
    stores: list[dict] = field(default_factory=list)  # {"store","uri","remote_path"}
    approved_by: str = ""
    dry_run: bool = True
    note: str = ""


class Journal:
    """Append-only JSONL audit journal."""

    def __init__(self, path: str) -> None:
        """Expand ``~`` in *path* and create its parent directory."""
        self.path = os.path.expanduser(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def append(self, entry: JournalEntry) -> None:
        """Append *entry* as one JSON line, flushed and fsync'd."""
        line = json.dumps(asdict(entry), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def entries(self) -> list[JournalEntry]:
        """Return all journal entries in append order."""
        result: list[JournalEntry] = []
        if not os.path.exists(self.path):
            return result
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                result.append(JournalEntry(**data))
        return result

    def render_markdown(self, entry: JournalEntry) -> str:
        """Render one journal entry as a disk-cleanup-log.md style bullet."""
        stores = ", ".join(
            f"{s.get('store', '?')}:{s.get('uri', s.get('remote_path', '?'))}" for s in entry.stores
        )
        short_sha = entry.sha256[:12] if entry.sha256 else ""
        note = f" {entry.note}" if entry.note else ""
        return (
            f"- {entry.ts} {entry.action} `{entry.path}` "
            f"({entry.size_bytes} bytes, sha256 `{short_sha}`) -> {stores} "
            f"approved_by={entry.approved_by} dry_run={entry.dry_run}{note}"
        )

    def import_registry(self, registry_path: str) -> int:
        """Import entries from a cloud_dog.py registry file.

        Reads the ``{"version": 1, "entries": {path: {...}}}`` format written
        by ``cloud_dog.py register`` (default
        ``~/workspace/agent-ops/cloud-dog-registry.json``) and appends one
        ``"store"`` journal entry per registry entry, so the journal becomes
        the single audit trail across both tools. Never modifies the registry
        file. Returns the number of entries imported (0 when the file is
        missing or unreadable).
        """
        path = os.path.expanduser(registry_path)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return 0
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        count = 0
        for local_path, reg in entries.items():
            if not isinstance(reg, dict):
                continue
            store = str(reg.get("store", ""))
            repo = str(reg.get("repo", ""))
            release = str(reg.get("release", ""))
            asset = str(reg.get("asset", ""))
            if store == "github" and repo and release and asset:
                uri = f"https://github.com/{repo}/releases/download/{release}/{asset}"
                remote_path = f"{release}/{asset}"
            else:
                uri = f"{store}:{asset or local_path}"
                remote_path = asset or ""
            self.append(
                JournalEntry(
                    ts=str(reg.get("registered_at", "")),
                    action="store",
                    path=str(reg.get("local_path", local_path)),
                    size_bytes=int(reg.get("size", 0) or 0),
                    sha256=str(reg.get("sha256", "")),
                    stores=[{"store": store, "uri": uri, "remote_path": remote_path}],
                    approved_by="cloud_dog.py",
                    dry_run=False,
                    note=f"imported from cloud-dog-registry.json "
                    f"(status={reg.get('status', '?')}"
                    f"{'; ' + reg['note'] if reg.get('note') else ''})",
                )
            )
            count += 1
        return count
