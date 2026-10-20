"""Read-side work-claim checks for the v1 engine.

The claims store of record is the sqlite registry maintained by the root
``cloud_dog_registry.py`` (its ``claims`` table — claim/heartbeat/release
live there). This module only READS that store: reapers ask "is this
path claimed?" before any delete. Semantics mirror the writer side
exactly: a claim is live while ``now - heartbeat_at <= ttl_s``, and a
claim on a directory covers everything under it.

Fail CLOSED: any failure to read the claims store (corrupt DB, lock,
I/O error) reports the path as claimed. If you can't read claims, you
don't reap. A *missing* DB (nobody has ever claimed anything) means no
claims — free. An existing DB with no ``claims`` table (a pre-claims
registry, or one swapped in under a long-lived process) is MIGRATED on
the spot (idempotent CREATE TABLE, mirroring cloud_dog_registry's
schema) and then read; if the migration itself is impossible the path
reports claimed — fail closed, aligned with the writer side's
``claim_gate`` (which never treated a missing table as "free").

CLI for non-CloudDog sweep lanes:

    python -m clouddog.claims check <path> [--db PATH]

Exit 0 = claimed / BLOCKED (including unreadable — fail closed),
exit 1 = free.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_CLAIMS_DB = os.path.expanduser("~/workspace/agent-ops/cloud-dog-registry.db")
ENV_CLAIMS_DB = "CLOUDEDOG_CLAIMS_DB"

# The claims table, byte-identical in shape to the writer side
# (cloud_dog_registry.py _SCHEMA_STATEMENTS). Kept as a literal here so
# this read-side module can self-migrate a pre-claims DB without
# importing the root package.
_CLAIMS_DDL = """
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
"""


def _ensure_claims_table(db_path) -> None:
    """Migration: create the claims table when a pre-claims DB lacks it.

    Idempotent. Raises on real failures (read-only DB, corrupt file) —
    the caller turns that into a fail-closed verdict.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute(_CLAIMS_DDL)
        conn.commit()
    finally:
        conn.close()


def _read_claim_rows(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn.execute(
            "SELECT path, owner, action, heartbeat_at, ttl_s FROM claims"
        ).fetchall()
    finally:
        conn.close()


def default_claims_db() -> str:
    return os.environ.get(ENV_CLAIMS_DB) or DEFAULT_CLAIMS_DB


def _parse_ts(text):
    try:
        dt = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def check_claimed(db_path, path):
    """(blocked, detail) for `path` against the claims DB at `db_path`.

    blocked=True when a live claim covers the path (exact or ancestor),
    or when the claims store exists but cannot be read (fail closed).
    """
    if not db_path or not os.path.exists(db_path):
        return False, ""
    norm = os.path.normpath(os.path.abspath(str(path)))
    try:
        try:
            rows = _read_claim_rows(db_path)
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            # Pre-claims registry: migrate (idempotent), then read for
            # real. A DB that cannot be migrated is unreadable — the
            # outer handler fails closed, like the writer side.
            _ensure_claims_table(db_path)
            rows = _read_claim_rows(db_path)
    except sqlite3.OperationalError as exc:
        return True, f"claims unreadable ({exc}); failing closed"
    except Exception as exc:  # noqa: BLE001 - any read failure blocks
        return True, f"claims unreadable ({exc}); failing closed"
    now = datetime.now(timezone.utc)
    for cpath, owner, action, heartbeat_at, ttl_s in rows:
        if not (norm == cpath or norm.startswith(cpath + os.sep)):
            continue
        hb = _parse_ts(heartbeat_at)
        if hb is None:
            continue  # unparseable heartbeat = dead claim, never live
        try:
            ttl = int(ttl_s or 0)
        except (TypeError, ValueError):
            ttl = 0
        if (now - hb).total_seconds() <= max(1, ttl):
            return True, (f"claimed by {owner} ({action}) heartbeat_at={heartbeat_at} ttl_s={ttl}")
    return False, ""


def make_claim_checker(db_path=None):
    """Build the Reaper ``claim_check`` callback bound to a claims DB."""
    db = db_path or default_claims_db()

    def _check(path):
        return check_claimed(db, path)

    return _check


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    db = default_claims_db()
    if "--db" in argv:
        i = argv.index("--db")
        if i + 1 >= len(argv):
            print("usage: python -m clouddog.claims check <path> [--db PATH]", file=sys.stderr)
            return 2
        db = argv[i + 1]
        del argv[i : i + 2]
    if len(argv) != 2 or argv[0] != "check":
        print("usage: python -m clouddog.claims check <path> [--db PATH]", file=sys.stderr)
        return 2
    blocked, detail = check_claimed(db, argv[1])
    if blocked:
        print(f"BLOCKED {os.path.abspath(argv[1])} — {detail}")
        return 0
    print(f"FREE {os.path.abspath(argv[1])}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
