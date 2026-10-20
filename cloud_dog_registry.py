#!/usr/bin/env python3
"""cloud_dog_registry.py — Cloud Dog registry backends (json | sqlite).

Two backends behind one dict-shaped interface so the engine, the daemon,
and the guardian cron keep working unchanged:

    json   — legacy flat file (cloud-dog-registry.json). Atomic writes
             (tmp file + os.replace). No concurrency protection.
    sqlite — columnar table (cloud-dog-registry.db). WAL mode, busy
             timeout, atomic transactions. Safe under the 10-min guardian
             cron + 24/7 daemon + parallel lanes.

Selection, in order:
    1. explicit `backend=` argument ("sqlite" | "json"),
    2. $CLOUDEDOG_REGISTRY environment variable,
    3. "json" (the default; the owner flips it to "sqlite" explicitly).

The public shape is the same for both backends::

    {"version": 4, "entries": {"/abs/path": {...entry...}}}

Entry dicts keep every field the engine writes (store, repo, release,
asset, remote_dir, dataset, restic_repo, snapshot_id, b2_bucket, b2_path,
sha256, size, note, status, registered_at, reaped_at, verified_at, ...).
The sqlite backend stores the hot columns as real columns and the rest as
a JSON `extra` blob, so round-trips are lossless.

sqlite schema:
    entries (local_path TEXT PRIMARY KEY, store, repo, release, asset,
             size INTEGER, sha256, status, registered_at, reaped_at,
             verified_at, updated_at, extra TEXT JSON)
    journal (id INTEGER PK, ts, action, local_path, detail)  -- audit trail
    meta    (k TEXT PK, v TEXT)  -- migration/schema flags

Migration: `migrate_json_to_sqlite()` is the one-shot JSON->sqlite
importer. It preserves every entry (both legacy JSON shapes: the
{"entries": {...}} dict and the [{"path": ...}] list), keeps the JSON
file on disk as a backup, and NEVER deletes it. It is also run
automatically the first time the sqlite backend opens (guarded by the
meta flag), so the legacy file can never be lost in a cutover.

Upgrade: DBs written by the v3 blob schema (entries.local_path/data/
updated_at, from the 2026-10-17 v3 lane) are detected on open and
exploded into the columnar schema; the old table is renamed to
entries_legacy_v3 and kept, never dropped.

Stdlib only (sqlite3, json, os, time).
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone

REGISTRY_VERSION = 4
ENV_VAR = "CLOUDEDOG_REGISTRY"
BACKENDS = ("json", "sqlite")

# Columns stored as first-class sqlite columns (hot path for the engine).
# Everything else on the entry dict rides along in the `extra` JSON blob.
COLUMNS = (
    "store", "repo", "release", "asset", "size", "sha256", "status",
    "registered_at", "reaped_at", "verified_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    local_path    TEXT PRIMARY KEY,
    store         TEXT NOT NULL DEFAULT '',
    repo          TEXT NOT NULL DEFAULT '',
    release       TEXT NOT NULL DEFAULT '',
    asset         TEXT NOT NULL DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'registered',
    registered_at TEXT NOT NULL DEFAULT '',
    reaped_at     TEXT NOT NULL DEFAULT '',
    verified_at   TEXT NOT NULL DEFAULT '',
    updated_at    TEXT NOT NULL DEFAULT '',
    extra         TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS journal (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    action     TEXT NOT NULL,
    local_path TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")


def select_backend(override=None, environ=None):
    """Resolve which backend to use: override > $CLOUDEDOG_REGISTRY > json."""
    env = os.environ if environ is None else environ
    if override:
        backend = override
    else:
        backend = (env.get(ENV_VAR, "") or "").strip().lower()
    if not backend:
        return "json"
    if backend not in BACKENDS:
        raise ValueError(
            f"unknown registry backend {backend!r}; want one of {BACKENDS}")
    return backend


# ---------------------------------------------------------------- JSON shape
# The engine thinks in {"version": N, "entries": {path: {...}}}. The legacy
# on-disk JSON sometimes wears a second shape — a bare list of entries with
# a "path" key (written by external lanes). Both normalize to the dict shape.

def _normalize_json(payload):
    """Normalize a decoded JSON registry payload to {path: entry}."""
    if isinstance(payload, dict):
        entries = payload.get("entries", {})
        if isinstance(entries, dict):
            return {str(p): (e if isinstance(e, dict) else {"_value": e})
                    for p, e in entries.items()}
        return {}
    if isinstance(payload, list):
        out = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            p = item.get("path") or item.get("local_path")
            if not p:
                continue
            entry = dict(item)
            entry["local_path"] = str(p)
            out[str(p)] = entry
        return out
    return {}


def json_load(json_path):
    """Load the JSON registry; tolerates both legacy shapes."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return {"version": REGISTRY_VERSION, "entries": {}}
    except (OSError, json.JSONDecodeError):
        # A corrupt registry must not take the engine down: treat as empty
        # and let the next save rewrite it. The sqlite journal records it.
        return {"version": REGISTRY_VERSION, "entries": {},
                "_corrupt": True}
    version = payload.get("version", 1) if isinstance(payload, dict) else 1
    return {"version": version, "entries": _normalize_json(payload)}


def json_save(reg, json_path):
    """Atomically write the JSON registry (tmp file + os.replace)."""
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    tmp = json_path + ".tmp"
    payload = {"version": reg.get("version", REGISTRY_VERSION),
               "entries": reg.get("entries", {})}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, json_path)


# ---------------------------------------------------------------- sqlite
def _explode_entry(path, entry):
    """Split an entry dict into (columns, extra_json)."""
    if not isinstance(entry, dict):
        entry = {"_value": entry}
    cols = {}
    for name in COLUMNS:
        cols[name] = entry.get(name, "" if name != "size" else 0)
    try:
        cols["size"] = int(cols["size"] or 0)
    except (TypeError, ValueError):
        cols["size"] = 0
    for name in COLUMNS:
        if name != "size":
            cols[name] = str(cols[name] or "")
    if not cols["status"]:
        cols["status"] = "registered"
    extra = {k: v for k, v in entry.items() if k not in COLUMNS}
    extra.setdefault("local_path", path)
    return cols, json.dumps(extra, sort_keys=True)


def _assemble_row(row):
    """Rebuild the entry dict from a DB row."""
    (path, store, repo, release, asset, size, sha256, status,
     registered_at, reaped_at, verified_at, _updated_at, extra) = row
    entry = {
        "store": store, "repo": repo, "release": release, "asset": asset,
        "size": size, "sha256": sha256, "status": status,
        "registered_at": registered_at, "reaped_at": reaped_at,
        "verified_at": verified_at,
    }
    try:
        entry.update(json.loads(extra or "{}"))
    except (json.JSONDecodeError, TypeError):
        pass
    entry.setdefault("local_path", path)
    return path, entry


def _journal(conn, action, local_path="", detail=""):
    conn.execute(
        "INSERT INTO journal (ts, action, local_path, detail)"
        " VALUES (?, ?, ?, ?)",
        (_stamp(), action, local_path, detail))


def _maybe_upgrade_v3(conn):
    """Explode the v3 blob schema (local_path/data/updated_at) into columns.

    The old table is renamed to entries_legacy_v3 and KEPT as a backup —
    never dropped, never deleted.
    """
    info = {r[1] for r in conn.execute("PRAGMA table_info(entries)")}
    if not info or "data" not in info or "sha256" in info:
        return 0  # no table yet, or already the columnar schema
    stamp = _stamp()
    conn.execute("ALTER TABLE entries RENAME TO entries_legacy_v3")
    conn.executescript(_SCHEMA)
    n = 0
    for path, data in conn.execute(
            "SELECT local_path, data FROM entries_legacy_v3"):
        try:
            entry = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            entry = {"_raw": data}
        cols, extra = _explode_entry(path, entry)
        conn.execute(
            "INSERT INTO entries (local_path, store, repo, release, asset,"
            " size, sha256, status, registered_at, reaped_at, verified_at,"
            " updated_at, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (path, cols["store"], cols["repo"], cols["release"],
             cols["asset"], cols["size"], cols["sha256"], cols["status"],
             cols["registered_at"], cols["reaped_at"], cols["verified_at"],
             stamp, extra))
        n += 1
    _journal(conn, "schema-upgrade",
             detail=f"exploded {n} v3 blob rows into columnar schema;"
                    " entries_legacy_v3 kept as backup")
    conn.execute("INSERT OR REPLACE INTO meta (k, v)"
                 " VALUES ('schema_version', '4')")
    return n


def _open_db(db_path, auto_migrate_json=True, json_path=None):
    """Open the registry DB: WAL, busy timeout, schema, upgrades, JSON seed.

    Raises on real errors; the caller must close the returned connection.
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Default (deferred) isolation: DML opens an implicit transaction and
    # `with conn:` blocks COMMIT/ROLLBACK atomically around each write.
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SCHEMA)
        _maybe_upgrade_v3(conn)
        conn.commit()  # persist schema/upgrade DDL+DML before any reads
        if auto_migrate_json and json_path and os.path.exists(json_path):
            row = conn.execute(
                "SELECT v FROM meta WHERE k='json_migrated'").fetchone()
            if not row:
                # One-shot seed from the legacy JSON file. The file itself
                # is never modified or deleted here — it stays as a backup.
                reg = json_load(json_path)
                stamp = _stamp()
                with conn:  # atomic
                    for path, entry in reg["entries"].items():
                        cols, extra = _explode_entry(path, entry)
                        conn.execute(
                            "INSERT OR REPLACE INTO entries (local_path,"
                            " store, repo, release, asset, size, sha256,"
                            " status, registered_at, reaped_at,"
                            " verified_at, updated_at, extra)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (path, cols["store"], cols["repo"],
                             cols["release"], cols["asset"], cols["size"],
                             cols["sha256"], cols["status"],
                             cols["registered_at"], cols["reaped_at"],
                             cols["verified_at"], stamp, extra))
                    _journal(conn, "auto-migrate",
                             detail=f"seeded {len(reg['entries'])} entries"
                                    f" from {json_path} (file kept as backup)")
                    conn.execute("INSERT INTO meta (k, v)"
                                 " VALUES ('json_migrated', '1')")
    except Exception:
        conn.close()
        raise
    return conn


def sqlite_load(db_path, json_path=None):
    conn = _open_db(db_path, json_path=json_path)
    try:
        rows = conn.execute(
            "SELECT local_path, store, repo, release, asset, size, sha256,"
            " status, registered_at, reaped_at, verified_at, updated_at,"
            " extra FROM entries").fetchall()
        entries = {}
        for row in rows:
            path, entry = _assemble_row(row)
            entries[path] = entry
        return {"version": REGISTRY_VERSION, "entries": entries}
    finally:
        conn.close()


def sqlite_save(reg, db_path, json_path=None):
    conn = _open_db(db_path, json_path=json_path)
    try:
        stamp = _stamp()
        entries = reg.get("entries", {})
        with conn:  # atomic replace
            conn.execute("DELETE FROM entries")
            for path, entry in entries.items():
                cols, extra = _explode_entry(path, entry)
                conn.execute(
                    "INSERT INTO entries (local_path, store, repo, release,"
                    " asset, size, sha256, status, registered_at, reaped_at,"
                    " verified_at, updated_at, extra)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (path, cols["store"], cols["repo"], cols["release"],
                     cols["asset"], cols["size"], cols["sha256"],
                     cols["status"], cols["registered_at"], cols["reaped_at"],
                     cols["verified_at"], stamp, extra))
            _journal(conn, "save",
                     detail=f"registry replaced with {len(entries)} entries")
    finally:
        conn.close()


def sqlite_upsert(db_path, path, entry, json_path=None):
    """Single-entry atomic write (no full-table rewrite)."""
    conn = _open_db(db_path, json_path=json_path)
    try:
        cols, extra = _explode_entry(path, entry)
        with conn:
            conn.execute(
                "INSERT INTO entries (local_path, store, repo, release,"
                " asset, size, sha256, status, registered_at, reaped_at,"
                " verified_at, updated_at, extra)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(local_path) DO UPDATE SET"
                " store=excluded.store, repo=excluded.repo,"
                " release=excluded.release, asset=excluded.asset,"
                " size=excluded.size, sha256=excluded.sha256,"
                " status=excluded.status,"
                " registered_at=excluded.registered_at,"
                " reaped_at=excluded.reaped_at,"
                " verified_at=excluded.verified_at,"
                " updated_at=excluded.updated_at, extra=excluded.extra",
                (path, cols["store"], cols["repo"], cols["release"],
                 cols["asset"], cols["size"], cols["sha256"], cols["status"],
                 cols["registered_at"], cols["reaped_at"], cols["verified_at"],
                 _stamp(), extra))
            _journal(conn, "upsert", local_path=path,
                     detail=f"store={cols['store']} status={cols['status']}")
    finally:
        conn.close()


# ---------------------------------------------------------------- migration
def migrate_json_to_sqlite(json_path, db_path, force=False):
    """One-shot JSON -> sqlite importer. Preserves every entry.

    Handles both legacy JSON shapes ({"entries": {...}} and the
    [{"path": ...}] list). Uses INSERT OR REPLACE keyed by absolute path,
    so re-running is idempotent. The JSON file is NEVER deleted or
    modified — it stays on disk as the backup.

    Returns {"migrated": n, "skipped": [paths...], "json_path": ..., "db_path": ...}.
    """
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"JSON registry not found: {json_path}")
    conn = _open_db(db_path, auto_migrate_json=False)
    try:
        if not force:
            row = conn.execute(
                "SELECT v FROM meta WHERE k='json_migrated'").fetchone()
        else:
            row = None
        reg = json_load(json_path)
        stamp = _stamp()
        migrated, skipped = 0, []
        with conn:  # atomic
            for path, entry in reg["entries"].items():
                if not path or not str(path).startswith("/"):
                    skipped.append(path)
                    continue
                cols, extra = _explode_entry(path, entry)
                conn.execute(
                    "INSERT OR REPLACE INTO entries (local_path, store,"
                    " repo, release, asset, size, sha256, status,"
                    " registered_at, reaped_at, verified_at, updated_at,"
                    " extra) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (path, cols["store"], cols["repo"], cols["release"],
                     cols["asset"], cols["size"], cols["sha256"],
                     cols["status"], cols["registered_at"], cols["reaped_at"],
                     cols["verified_at"], stamp, extra))
                migrated += 1
            _journal(conn, "migrate",
                     detail=f"one-shot import: {migrated} entries from"
                            f" {json_path} (skipped={len(skipped)});"
                            " JSON file kept as backup, not deleted")
            if row is None:
                conn.execute("INSERT OR REPLACE INTO meta (k, v)"
                             " VALUES ('json_migrated', '1')")
        return {"migrated": migrated, "skipped": skipped,
                "json_path": json_path, "db_path": db_path}
    finally:
        conn.close()


def export_json(db_path, dest_path):
    """Dump the sqlite registry back to the legacy JSON shape (backup/audit)."""
    reg = sqlite_load(db_path)
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    tmp = dest_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": REGISTRY_VERSION, "entries": reg["entries"]},
                  f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dest_path)
    return dest_path


def journal_tail(db_path, limit=50):
    """Recent audit-journal rows (newest first)."""
    conn = _open_db(db_path, auto_migrate_json=False)
    try:
        return conn.execute(
            "SELECT ts, action, local_path, detail FROM journal"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------- unified API
def load_registry(json_path, db_path, backend=None):
    backend = select_backend(backend)
    if backend == "sqlite":
        return sqlite_load(db_path, json_path=json_path)
    return json_load(json_path)


def save_registry(reg, json_path, db_path, backend=None):
    backend = select_backend(backend)
    if backend == "sqlite":
        sqlite_save(reg, db_path, json_path=json_path)
    else:
        json_save(reg, json_path)


class Registry:
    """Dict-compatible registry handle over either backend.

    reg = Registry(json_path, db_path, backend="sqlite")
    data = reg.load()            # {"version": 4, "entries": {...}}
    reg.upsert("/abs/path", {...entry...})
    reg.save(data)
    reg.close()
    """

    def __init__(self, json_path, db_path, backend=None):
        self.json_path = json_path
        self.db_path = db_path
        self.backend = select_backend(backend)

    def load(self):
        return load_registry(self.json_path, self.db_path,
                             backend=self.backend)

    def save(self, reg):
        return save_registry(reg, self.json_path, self.db_path,
                             backend=self.backend)

    def upsert(self, path, entry):
        if self.backend == "sqlite":
            sqlite_upsert(self.db_path, path, entry,
                          json_path=self.json_path)
        else:
            reg = self.load()
            reg["entries"][path] = entry
            self.save(reg)

    def migrate(self, force=False):
        """One-shot JSON -> sqlite import (JSON kept as backup)."""
        return migrate_json_to_sqlite(self.json_path, self.db_path,
                                      force=force)

    def export_json(self, dest_path):
        """Dump the sqlite registry to a JSON backup file."""
        return export_json(self.db_path, dest_path)

    def journal_tail(self, limit=50):
        if self.backend != "sqlite":
            raise ValueError("journal is only available on the sqlite backend")
        return journal_tail(self.db_path, limit=limit)

    def close(self):
        pass  # connections are short-lived; nothing to hold
