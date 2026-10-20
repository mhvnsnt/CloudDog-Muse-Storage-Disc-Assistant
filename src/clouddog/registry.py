"""Registry access for the v1 engine (daemon / reaper / streamer).

The canonical registry (repo AGENTS.md, "Registry contract") is
``~/workspace/agent-ops/cloud-dog-registry.json`` — entries keyed by
absolute local path: {store, repo, release, asset, sha256, size,
status, registered_at, ...}. A sqlite mirror (schema v4, maintained by
the root ``cloud_dog_registry.py``) lives alongside it at
``cloud-dog-registry.db``.

The v1 daemon historically never touched either store — it stored,
verified, and deleted entirely from its own tick state, which is how
the 2026-10-20 incident deleted a directory tree with NO registry
entry. This client gives the v1 package stdlib-only registry access
for the delete gate (``clouddog.delete_gate``):

- :func:`lookup_entry` — JSON first, then the sqlite entries table
  (read-only; lanes may register through either surface).
- :func:`register_entry` — atomic read-modify-write of the JSON
  registry (tmp file + os.replace), the same file lanes use via
  ``cloud_dog.py register``.
- :func:`mark_reaped` — flip a JSON entry to status "reaped" after a
  gated delete (sqlite-only entries are left to cloud_dog.py's own
  bookkeeping, which marks missing files reaped on its next pass).

All functions fail soft on read errors (lookup returns None), because
the delete gate treats "no entry found" as a hard block — a registry
that cannot be read therefore fails CLOSED at the gate, never open.
Writes raise: a daemon that cannot record its store must know.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

DEFAULT_REGISTRY_JSON = os.path.expanduser(
    "~/workspace/agent-ops/cloud-dog-registry.json"
)
DEFAULT_REGISTRY_DB = os.path.expanduser(
    "~/workspace/agent-ops/cloud-dog-registry.db"
)
ENV_REGISTRY_JSON = "CLOUDEDOG_REGISTRY_JSON"
ENV_REGISTRY_DB = "CLOUDEDOG_REGISTRY_DB"


def default_registry_json() -> str:
    return os.environ.get(ENV_REGISTRY_JSON) or DEFAULT_REGISTRY_JSON


def default_registry_db() -> str:
    return os.environ.get(ENV_REGISTRY_DB) or DEFAULT_REGISTRY_DB


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(json_path: str) -> dict:
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict):
        return {"version": 1, "entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _save_json(json_path: str, data: dict) -> None:
    parent = os.path.dirname(os.path.abspath(json_path))
    os.makedirs(parent, exist_ok=True)
    tmp = json_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, json_path)


def _lookup_json(path: str, json_path: str) -> dict | None:
    entry = _load_json(json_path)["entries"].get(os.path.abspath(path))
    return dict(entry) if isinstance(entry, dict) else None


def _lookup_sqlite(path: str, db_path: str) -> dict | None:
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000;")
            row = conn.execute(
                "SELECT store, repo, release, asset, size, sha256, status, extra"
                " FROM entries WHERE local_path=?",
                (os.path.abspath(path),),
            ).fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - unreadable registry = no entry (gate fails closed)
        return None
    if row is None:
        return None
    store, repo, release, asset, size, sha256, status, extra = row
    entry: dict = {
        "store": store or "",
        "repo": repo or "",
        "release": release or "",
        "asset": asset or "",
        "size": size or 0,
        "sha256": sha256 or "",
        "status": status or "registered",
    }
    try:
        extra_data = json.loads(extra) if extra else {}
    except (TypeError, ValueError):
        extra_data = {}
    if isinstance(extra_data, dict):
        for key, value in extra_data.items():
            entry.setdefault(key, value)
    return entry


def lookup_entry(
    path: str,
    json_path: str | None = None,
    db_path: str | None = None,
) -> dict | None:
    """The registry entry for ``path``, or None. JSON first, then sqlite."""
    entry = _lookup_json(path, json_path or default_registry_json())
    if entry is not None:
        return entry
    return _lookup_sqlite(path, db_path or default_registry_db())


def register_entry(
    path: str,
    entry: dict,
    json_path: str | None = None,
) -> dict:
    """Insert (or replace) the JSON registry entry for ``path``.

    Returns the entry as stored. The caller supplies the store record
    (store/locator/sha256/size from a verified store); this function
    stamps status/registered_at defaults.
    """
    json_path = json_path or default_registry_json()
    data = _load_json(json_path)
    stored = dict(entry)
    stored.setdefault("status", "registered")
    stored.setdefault("registered_at", _utcnow())
    data["entries"][os.path.abspath(path)] = stored
    _save_json(json_path, data)
    return stored


def mark_reaped(path: str, json_path: str | None = None) -> bool:
    """Flip an existing JSON entry to status "reaped". True if updated."""
    json_path = json_path or default_registry_json()
    data = _load_json(json_path)
    key = os.path.abspath(path)
    entry = data["entries"].get(key)
    if not isinstance(entry, dict):
        return False
    entry["status"] = "reaped"
    entry["reaped_at"] = _utcnow()
    _save_json(json_path, data)
    return True
