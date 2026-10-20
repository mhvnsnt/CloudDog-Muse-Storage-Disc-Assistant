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
    claims  (path TEXT PK, owner, action, claim_id, epoch, claimed_at,
             heartbeat_at, ttl_s, note)  -- work-claim leases: lane X owns
             path Y until heartbeat+TTL lapses. Reapers MUST check this
             table before deleting (claim_gate); a live claim BLOCKS a
             reap. Written because a cleanup lane deleted an in-progress
             Tekken 5 ISO whose only protection was the 5-minute mtime
             heuristic in is_inflight() — a paused/resumable download
             older than 5 minutes looked reapable. Claims are
             authoritative; the heuristic stays as a second net.

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

Concurrency (the CI "database is locked" flake fix, 2026-10-20):
Python's sqlite3 default starts transactions DEFERRED; a deferred
read that upgrades to a write while another connection holds the write
lock gets SQLITE_BUSY *immediately*, bypassing busy_timeout entirely
(SQLite deadlock avoidance). So this module connects with
isolation_level=None (autocommit) and controls transactions itself:
every write runs under an explicit BEGIN IMMEDIATE (the write lock is
taken up front, where busy_timeout actually applies), whole write
operations retry on residual "locked" errors with exponential backoff
+ jitter, and first-open schema DDL is serialized behind a
process-wide lock + a cross-process flock so N racing openers no
longer stampede CREATE TABLE / the v3 upgrade / the JSON seed.

Stdlib only (sqlite3, json, os, time).
"""

import contextlib
import json
import os
import random
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # non-POSIX: the threading lock still serializes in-process
    fcntl = None

REGISTRY_VERSION = 4
ENV_VAR = "CLOUDEDOG_REGISTRY"
BACKENDS = ("json", "sqlite")

# Columns stored as first-class sqlite columns (hot path for the engine).
# Everything else on the entry dict rides along in the `extra` JSON blob.
COLUMNS = (
    "store", "repo", "release", "asset", "size", "sha256", "status",
    "registered_at", "reaped_at", "verified_at",
)

# Individual statements (not one executescript blob) so schema init can
# run inside an explicit BEGIN IMMEDIATE transaction — executescript
# implicitly COMMITs any pending transaction first, which would break it.
_SCHEMA_STATEMENTS = (
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS journal (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        ts         TEXT NOT NULL,
        action     TEXT NOT NULL,
        local_path TEXT NOT NULL DEFAULT '',
        detail     TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    )
    """,
    # Work claims (lane-coordination, the Tekken 5 fix): "lane X owns
    # path Y until the lease lapses". Reapers MUST consult this table
    # before deleting (see the claims API + claim_gate below).
    """
    CREATE TABLE IF NOT EXISTS claims (
        path         TEXT PRIMARY KEY,
        owner        TEXT NOT NULL,
        action       TEXT NOT NULL,
        claim_id     TEXT NOT NULL UNIQUE,
        epoch        INTEGER NOT NULL,
        claimed_at   TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        ttl_s        INTEGER NOT NULL DEFAULT 600,
        note         TEXT NOT NULL DEFAULT ''
    )
    """,
)

_SCHEMA = ";\n".join(s.strip() for s in _SCHEMA_STATEMENTS) + ";\n"


# ------------------------------------------------------- sqlite locking
# See the module docstring: DEFERRED lock-upgrades bypass busy_timeout,
# so writes take the lock up front (BEGIN IMMEDIATE) and whole operations
# retry on residual lock contention. Schema init is serialized so racing
# first-openers don't stampede the DDL.

_BUSY_ATTEMPTS = 5
_BUSY_BASE_S = 0.05
_BUSY_CAP_S = 2.0

_SCHEMA_INIT_LOCK = threading.Lock()
_schema_init_done = set()  # abspaths whose init completed in this process


def _is_locked(exc):
    return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()


def _retry_locked(fn, attempts=_BUSY_ATTEMPTS):
    """Run fn(); retry sqlite "database is locked" with backoff + jitter."""
    for attempt in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc) or attempt == attempts - 1:
                raise
            delay = min(_BUSY_CAP_S, _BUSY_BASE_S * (2**attempt))
            time.sleep(delay + random.uniform(0, _BUSY_BASE_S))


@contextlib.contextmanager
def _write_txn(conn):
    """Explicit BEGIN IMMEDIATE write transaction on an autocommit conn.

    BEGIN IMMEDIATE takes the write lock up front, so busy_timeout
    applies; a DEFERRED transaction upgrading mid-flight would return
    SQLITE_BUSY immediately instead. Rolls back on any error so callers
    can retry the whole operation via _retry_locked.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextlib.contextmanager
def _schema_init_guard(db_path):
    """Serialize first-open schema init across threads AND processes."""
    with _SCHEMA_INIT_LOCK:
        fh = None
        if fcntl is not None:
            fh = open(db_path + ".init.lock", "a+")  # noqa: SIM115 - fd held across the yield, closed in finally
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fh is not None:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                finally:
                    fh.close()


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
    for stmt in _SCHEMA_STATEMENTS:
        conn.execute(stmt)
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


def _seed_from_json(conn, json_path):
    """One-shot seed from the legacy JSON file inside an open write txn.

    The file itself is never modified or deleted here — it stays as a backup.
    Idempotent: guarded by the json_migrated meta flag.
    """
    row = conn.execute("SELECT v FROM meta WHERE k='json_migrated'").fetchone()
    if row:
        return
    reg = json_load(json_path)
    stamp = _stamp()
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
    conn.execute("INSERT INTO meta (k, v) VALUES ('json_migrated', '1')")


def _init_schema(conn, db_path, auto_migrate_json, json_path):
    """First-open schema/upgrade/seed, serialized and lock-retried.

    The schema DDL runs only under the init guard — racing openers no
    longer stampede CREATE TABLE / the v3 upgrade. A per-process flag
    skips the guard on later opens of the same DB. The JSON-seed check
    is NOT cached: it stays an every-open meta-flag read (cheap SELECT),
    matching the pre-flake-fix behavior exactly, and only takes the
    write path when the DB has never been seeded.
    """
    key = os.path.abspath(db_path)
    if key not in _schema_init_done:
        with _schema_init_guard(db_path):
            if key not in _schema_init_done:

                def _do_schema():
                    # WAL is persistent in the DB header, so it is set
                    # ONCE, here — never per-open. The delete->WAL
                    # transition needs an exclusive lock and returns
                    # SQLITE_BUSY *immediately* (busy_timeout does not
                    # cover it) while another opener holds any lock;
                    # that is why this must live inside the serialized,
                    # retried init and not in _open_db's pragma block.
                    conn.execute("PRAGMA journal_mode=WAL;")
                    with _write_txn(conn):
                        for stmt in _SCHEMA_STATEMENTS:
                            conn.execute(stmt)
                        _maybe_upgrade_v3(conn)

                _retry_locked(_do_schema)
                _schema_init_done.add(key)
    if auto_migrate_json and json_path and os.path.exists(json_path):
        row = conn.execute(
            "SELECT v FROM meta WHERE k='json_migrated'").fetchone()
        if not row:
            def _do_seed():
                with _write_txn(conn):
                    _seed_from_json(conn, json_path)

            with _schema_init_guard(db_path):
                _retry_locked(_do_seed)


def _open_db(db_path, auto_migrate_json=True, json_path=None):
    """Open the registry DB: WAL, busy timeout, schema, upgrades, JSON seed.

    Raises on real errors; the caller must close the returned connection.

    The connection is in autocommit mode (isolation_level=None): writes
    take an explicit BEGIN IMMEDIATE via _write_txn so the lock is held up
    front where busy_timeout applies (the DEFERRED upgrade path bypasses
    it entirely — the root cause of the CI "database is locked" flake).
    """
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        # journal_mode=WAL is set inside _init_schema (serialized +
        # retried): the mode transition needs an exclusive lock and
        # bypasses busy_timeout, so per-open execution raced (CI flake).
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _init_schema(conn, db_path, auto_migrate_json, json_path)
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

        def _do_save():
            with _write_txn(conn):  # atomic replace
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

        _retry_locked(_do_save)
    finally:
        conn.close()


def sqlite_upsert(db_path, path, entry, json_path=None):
    """Single-entry atomic write (no full-table rewrite)."""
    conn = _open_db(db_path, json_path=json_path)
    try:
        cols, extra = _explode_entry(path, entry)

        def _do_upsert():
            with _write_txn(conn):
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

        _retry_locked(_do_upsert)
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
        result = {}

        def _do_migrate():
            migrated, skipped = 0, []
            with _write_txn(conn):  # atomic
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
            result.update(migrated=migrated, skipped=skipped)

        _retry_locked(_do_migrate)
        return {"migrated": result["migrated"], "skipped": result["skipped"],
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


# ---------------------------------------------------------------- claims
# Work claims: leased, heartbeated ownership records in the SAME sqlite
# registry (no second store). A lane claims a path (or a whole directory)
# when it starts work; every delete path must consult claim_gate() first.
# A live claim is a HARD BLOCK, never advisory. Leases die on their own
# when heartbeats stop (ttl_s), so a crashed lane never wedges the disk.
#
# Fencing: every successful claim issues a fresh uuid4 claim_id and a
# per-path monotonically increasing epoch (kept in meta so it survives
# release). heartbeat/release must present the claim_id they were
# issued — a stale holder whose lease lapsed and was re-claimed cannot
# revive or release the new owner's claim.
#
# Claims are sqlite-only BY DESIGN (single source of truth). The JSON
# entries backend is not mirrored: two claim stores that can disagree
# would be worse than one. Functions take db_path directly, whatever
# backend the entries use.

DEFAULT_CLAIM_TTL_S = 600
_CLAIM_EPOCH_META_PREFIX = "claim_epoch:"
_CLAIM_COLS = ("path, owner, action, claim_id, epoch, claimed_at,"
               " heartbeat_at, ttl_s, note")


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


def _iso_parse(text):
    """Parse an ISO-8601 stamp to an aware datetime; None if unparseable."""
    try:
        dt = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_claim_path(path):
    return os.path.normpath(os.path.abspath(str(path)))


def _claim_row_to_dict(row):
    (path, owner, action, claim_id, epoch, claimed_at, heartbeat_at,
     ttl_s, note) = row
    return {
        "path": path, "owner": owner, "action": action,
        "claim_id": claim_id, "epoch": int(epoch),
        "claimed_at": claimed_at, "heartbeat_at": heartbeat_at,
        "ttl_s": int(ttl_s), "note": note,
    }


def _claim_is_live(claim, now=None):
    now = now or datetime.now(timezone.utc)
    hb = _iso_parse(claim["heartbeat_at"])
    if hb is None:
        return False  # unparseable heartbeat = dead claim, never a live one
    return (now - hb).total_seconds() <= max(1, int(claim["ttl_s"]))


def claim_path(db_path, path, owner, action, note="",
               ttl_s=DEFAULT_CLAIM_TTL_S, json_path=None):
    """Claim `path` for `owner`. Returns (True, claim) on success.

    Returns (False, existing) when a DIFFERENT owner holds a live claim —
    the caller must defer, never force. Re-claiming your own live claim
    (e.g. a lane restarting) succeeds and issues a fresh claim_id +
    epoch, fencing off the previous incarnation. Claiming over an
    EXPIRED claim succeeds the same way.
    """
    norm = _normalize_claim_path(path)
    ttl_s = int(ttl_s)
    if ttl_s <= 0:
        raise ValueError("ttl_s must be positive")
    if not owner or not action:
        raise ValueError("owner and action are required")
    conn = _open_db(db_path, json_path=json_path)
    try:
        outcome = {}

        def _do_claim():
            with _write_txn(conn):
                row = conn.execute(
                    f"SELECT {_CLAIM_COLS} FROM claims WHERE path=?",
                    (norm,)).fetchone()
                existing = _claim_row_to_dict(row) if row else None
                if (existing and existing["owner"] != owner
                        and _claim_is_live(existing)):
                    outcome.update(claimed=False, claim=existing)
                    return
                erow = conn.execute(
                    "SELECT v FROM meta WHERE k=?",
                    (_CLAIM_EPOCH_META_PREFIX + norm,)).fetchone()
                epoch = (int(erow[0]) if erow else 0) + 1
                conn.execute(
                    "INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)",
                    (_CLAIM_EPOCH_META_PREFIX + norm, str(epoch)))
                now = _iso_now()
                claim = {
                    "path": norm, "owner": str(owner), "action": str(action),
                    "claim_id": uuid.uuid4().hex, "epoch": epoch,
                    "claimed_at": now, "heartbeat_at": now,
                    "ttl_s": ttl_s, "note": str(note or ""),
                }
                conn.execute(
                    "INSERT OR REPLACE INTO claims (path, owner, action,"
                    " claim_id, epoch, claimed_at, heartbeat_at, ttl_s,"
                    " note) VALUES (?,?,?,?,?,?,?,?,?)",
                    (norm, claim["owner"], claim["action"],
                     claim["claim_id"], epoch, now, now, ttl_s,
                     claim["note"]))
                _journal(conn, "claim", local_path=norm,
                         detail=(f"owner={claim['owner']}"
                                 f" action={claim['action']}"
                                 f" epoch={epoch} ttl_s={ttl_s}"
                                 f" note={claim['note']}")[:300])
                outcome.update(claimed=True, claim=claim)

        _retry_locked(_do_claim)
        return outcome["claimed"], outcome["claim"]
    finally:
        conn.close()


def heartbeat_claim(db_path, path, claim_id, json_path=None):
    """Renew a claim's lease. True only for the current claim_id holder:
    a stale holder (lease lapsed, path re-claimed) is rejected."""
    norm = _normalize_claim_path(path)
    conn = _open_db(db_path, json_path=json_path)
    try:
        result = {}

        def _do_hb():
            with _write_txn(conn):
                cur = conn.execute(
                    "UPDATE claims SET heartbeat_at=? WHERE path=?"
                    " AND claim_id=?",
                    (_iso_now(), norm, claim_id))
                result["ok"] = cur.rowcount == 1

        _retry_locked(_do_hb)
        return result["ok"]
    finally:
        conn.close()


def release_claim(db_path, path, claim_id, json_path=None):
    """Release a claim. Idempotent and claim_id-guarded: releasing with a
    stale claim_id deletes nothing and returns False."""
    norm = _normalize_claim_path(path)
    conn = _open_db(db_path, json_path=json_path)
    try:
        result = {}

        def _do_release():
            with _write_txn(conn):
                row = conn.execute(
                    "SELECT owner, epoch FROM claims WHERE path=?"
                    " AND claim_id=?", (norm, claim_id)).fetchone()
                if not row:
                    result["ok"] = False
                    return
                conn.execute(
                    "DELETE FROM claims WHERE path=? AND claim_id=?",
                    (norm, claim_id))
                _journal(conn, "claim-release", local_path=norm,
                         detail=f"owner={row[0]} epoch={row[1]}")
                result["ok"] = True

        _retry_locked(_do_release)
        return result["ok"]
    finally:
        conn.close()


def get_claim(db_path, path, json_path=None, include_expired=False):
    """The claim covering `path`: exact match, or a claim on any ancestor
    directory (a lane working a whole tree claims the tree root). Returns
    the most specific LIVE claim dict, or None."""
    norm = _normalize_claim_path(path)
    conn = _open_db(db_path, json_path=json_path)
    try:
        rows = conn.execute(f"SELECT {_CLAIM_COLS} FROM claims").fetchall()
    finally:
        conn.close()
    best = None
    for row in rows:
        claim = _claim_row_to_dict(row)
        cp = claim["path"]
        if norm == cp or norm.startswith(cp + os.sep):
            if include_expired or _claim_is_live(claim):
                if best is None or len(cp) > len(best["path"]):
                    best = claim
    return best


def is_claimed(db_path, path, json_path=None):
    """(True, claim) when a live claim covers `path`, else (False, None)."""
    claim = get_claim(db_path, path, json_path=json_path)
    return (claim is not None), claim


def list_claims(db_path, json_path=None, include_expired=True):
    """All claims, each annotated with a computed "live" flag."""
    conn = _open_db(db_path, json_path=json_path)
    try:
        rows = conn.execute(f"SELECT {_CLAIM_COLS} FROM claims").fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        claim = _claim_row_to_dict(row)
        claim["live"] = _claim_is_live(claim)
        if include_expired or claim["live"]:
            out.append(claim)
    return out


def expire_claims(db_path, json_path=None):
    """Delete claims whose lease lapsed (heartbeat older than ttl_s).
    Journaled per claim. Returns the number of claims expired."""
    conn = _open_db(db_path, json_path=json_path)
    try:
        result = {}

        def _do_expire():
            with _write_txn(conn):
                rows = conn.execute(
                    f"SELECT {_CLAIM_COLS} FROM claims").fetchall()
                dead = [c for c in (_claim_row_to_dict(r) for r in rows)
                        if not _claim_is_live(c)]
                for c in dead:
                    conn.execute(
                        "DELETE FROM claims WHERE path=? AND claim_id=?",
                        (c["path"], c["claim_id"]))
                    _journal(conn, "claim-expire", local_path=c["path"],
                             detail=(f"owner={c['owner']} epoch={c['epoch']}"
                                     f" heartbeat_at={c['heartbeat_at']}"
                                     f" ttl_s={c['ttl_s']}"))
                result["n"] = len(dead)

        _retry_locked(_do_expire)
        return result["n"]
    finally:
        conn.close()


def claims_readable(db_path, json_path=None):
    """(True, "") when the claims store can be read; (False, reason) else.

    Reap lanes probe this once up front so a broken claims store produces
    one loud "REAP DISABLED" instead of silently looking like "nothing to
    reap" (or worse, reaping blind).
    """
    try:
        conn = _open_db(db_path, json_path=json_path)
        try:
            conn.execute("SELECT COUNT(*) FROM claims").fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - any failure means unreadable
        return False, f"claims unreadable ({exc}); failing closed"
    return True, ""


def claim_gate(db_path, path, json_path=None):
    """Reaper pre-delete gate. Returns (allowed, reason).

    allowed is True ONLY when the claims store was read successfully AND
    no live claim covers `path`. ANY failure to read claims (missing or
    corrupt DB, lock exhaustion, anything) returns (False, reason):
    fail CLOSED. If you can't read claims, you don't reap.
    """
    try:
        claimed, claim = is_claimed(db_path, path, json_path=json_path)
    except Exception as exc:  # noqa: BLE001 - any read failure blocks
        return False, f"claims unreadable ({exc}); failing closed"
    if claimed:
        return False, (f"claimed by {claim['owner']} ({claim['action']});"
                       f" claimed_at={claim['claimed_at']}"
                       f" heartbeat_at={claim['heartbeat_at']}"
                       f" ttl_s={claim['ttl_s']}")
    return True, ""


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

    # -- work claims (sqlite store regardless of the entries backend) --

    def claim(self, path, owner, action, note="", ttl_s=DEFAULT_CLAIM_TTL_S):
        return claim_path(self.db_path, path, owner, action, note=note,
                          ttl_s=ttl_s, json_path=self.json_path)

    def heartbeat_claim(self, path, claim_id):
        return heartbeat_claim(self.db_path, path, claim_id,
                               json_path=self.json_path)

    def release_claim(self, path, claim_id):
        return release_claim(self.db_path, path, claim_id,
                             json_path=self.json_path)

    def get_claim(self, path, include_expired=False):
        return get_claim(self.db_path, path, json_path=self.json_path,
                         include_expired=include_expired)

    def expire_claims(self):
        return expire_claims(self.db_path, json_path=self.json_path)

    def close(self):
        pass  # connections are short-lived; nothing to hold
