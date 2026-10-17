"""Bridge: fold ``cloud_dog.py``'s registry into the v2 audit journal.

``cloud_dog.py`` keeps its registry at
``~/workspace/agent-ops/cloud-dog-registry.json``::

    {"version": 1, "entries": {"/abs/path": {"store": ..., "sha256": ...}}}

The v2 audit journal (``cloud-dog-audit.jsonl``) is the machine-readable
twin of the human-readable ``disk-cleanup-log.md``. This module converts
registry entries into audit-journal events so registry history becomes
part of the single audit trail — without modifying ``cloud_dog.py`` or
the registry file itself.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

DEFAULT_REGISTRY_PATH = os.path.expanduser("~/workspace/agent-ops/cloud-dog-registry.json")
DEFAULT_AUDIT_PATH = os.path.expanduser("~/workspace/agent-ops/cloud-dog-audit.jsonl")


def registry_entry_to_audit_event(local_path: str, entry: dict) -> dict:
    """Convert one registry entry to a v2 audit-journal event dict.

    The event mirrors v2's ``audit_log`` shape: ``ts`` (UTC ISO, seconds),
    ``action``, then sorted fields. Store-specific locators follow the
    registry contract documented in the repo's AGENTS.md.
    """
    store = str(entry.get("store", ""))
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": "registry-import",
        "local_path": str(entry.get("local_path", local_path)),
        "store": store,
        "sha256": str(entry.get("sha256", "")),
        "size": int(entry.get("size", 0) or 0),
        "status": str(entry.get("status", "")),
        "registered_at": str(entry.get("registered_at", "")),
        "note": str(entry.get("note", "")),
        "imported_by": "clouddog.bridge",
    }
    if store == "github":
        event["repo"] = str(entry.get("repo", ""))
        event["release"] = str(entry.get("release", ""))
        event["asset"] = str(entry.get("asset", ""))
    elif store == "terabox":
        event["remote_dir"] = str(entry.get("remote_dir", ""))
        event["asset"] = str(entry.get("asset", ""))
    elif store == "kaggle":
        event["dataset"] = str(entry.get("dataset", ""))
    return event


def import_registry_to_audit(
    registry_path: str = DEFAULT_REGISTRY_PATH,
    audit_path: str = DEFAULT_AUDIT_PATH,
) -> int:
    """Append one audit event per registry entry. Returns the count imported.

    Never raises: a missing or unreadable registry returns 0, and a failed
    event write is skipped. The registry file itself is never modified.
    """
    try:
        with open(os.path.expanduser(registry_path), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0
    entries = data.get("entries", {}) if isinstance(data, dict) else {}
    audit_path = os.path.expanduser(audit_path)
    count = 0
    for local_path, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        event = registry_entry_to_audit_event(local_path, entry)
        try:
            with open(audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
        except OSError:
            continue
        count += 1
    return count
