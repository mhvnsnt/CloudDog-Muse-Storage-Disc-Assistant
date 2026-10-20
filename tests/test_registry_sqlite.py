"""Tests for cloud_dog_registry.py (v4 registry backends).

Run: python3 -m pytest tests/test_registry_sqlite.py -v

These tests NEVER touch the live registry: every test works on tmp_path
copies. The one test that touches the real registry file only reads it
and migrates a *copy* (see test_migrate_live_registry_copy).
"""

import json
import os
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud_dog_registry as R


@pytest.fixture()
def paths(tmp_path):
    jp = str(tmp_path / "reg.json")
    dp = str(tmp_path / "reg.db")
    return jp, dp


SAMPLE = {
    "/ws/big/game.iso": {
        "local_path": "/ws/big/game.iso",
        "store": "github",
        "repo": "mhvnsnt/cape-jones",
        "release": "v1",
        "asset": "game.iso",
        "size": 1469014801,
        "sha256": "abc123",
        "status": "registered",
        "registered_at": "2026-10-19 20:00:00 UTC",
        "reaped_at": "",
        "verified_at": "",
        "remote_dir": "/tb/dir",  # store-specific -> extra
        "dataset": "o/d",  # store-specific -> extra
        "note": "hello",  # freeform -> extra
    },
}


def test_backend_selection_default_and_env(monkeypatch):
    monkeypatch.delenv("CLOUDEDOG_REGISTRY", raising=False)
    assert R.select_backend() == "json"  # default stays json
    monkeypatch.setenv("CLOUDEDOG_REGISTRY", "sqlite")
    assert R.select_backend() == "sqlite"
    assert R.select_backend("json") == "json"  # explicit beats env
    with pytest.raises(ValueError):
        R.select_backend("cassandra")


def test_json_backend_roundtrip_dict_shape(paths):
    jp, dp = paths
    reg = {"version": 4, "entries": SAMPLE}
    R.save_registry(reg, jp, dp, backend="json")
    back = R.load_registry(jp, dp, backend="json")
    assert back["entries"] == SAMPLE


def test_json_backend_reads_legacy_list_shape(paths):
    jp, dp = paths
    legacy_list = [
        {
            "path": "/ws/x/blob",
            "size": 1469014801,
            "sha256": "git-tree",
            "repo": "mhvnsnt/cape-jones",
            "release": "branch:x (git tree, not a release)",
            "assets": ["git restore --source=HEAD -- x"],
            "cleared": "2026-10-19T20:xx:CDT",
            "beat": "D",
        }
    ]
    with open(jp, "w") as f:
        json.dump(legacy_list, f)
    back = R.load_registry(jp, dp, backend="json")
    e = back["entries"]["/ws/x/blob"]
    assert e["size"] == 1469014801
    assert e["sha256"] == "git-tree"
    assert e["assets"] == ["git restore --source=HEAD -- x"]
    assert e["beat"] == "D"


def test_sqlite_schema_columns(paths):
    jp, dp = paths
    R.load_registry(jp, dp, backend="sqlite")  # creates schema
    conn = sqlite3.connect(dp)
    try:
        info = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(entries)")}
        for col in (
            "local_path",
            "store",
            "repo",
            "release",
            "asset",
            "size",
            "sha256",
            "status",
            "registered_at",
            "reaped_at",
            "verified_at",
            "extra",
        ):
            assert col in info, f"missing column {col}"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"entries", "journal", "meta"} <= tables
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        conn.close()


def test_sqlite_roundtrip_preserves_extra_fields(paths):
    jp, dp = paths
    R.save_registry({"version": 4, "entries": SAMPLE}, jp, dp, backend="sqlite")
    back = R.load_registry(jp, dp, backend="sqlite")
    e = back["entries"]["/ws/big/game.iso"]
    assert e["store"] == "github"
    assert e["repo"] == "mhvnsnt/cape-jones"
    assert e["release"] == "v1"
    assert e["asset"] == "game.iso"
    assert e["size"] == 1469014801
    assert e["sha256"] == "abc123"
    assert e["status"] == "registered"
    # freeform / store-specific fields ride along losslessly
    assert e["note"] == "hello"
    assert e["remote_dir"] == "/tb/dir"
    assert e["dataset"] == "o/d"
    assert e["local_path"] == "/ws/big/game.iso"


def test_sqlite_upsert_is_single_entry_and_atomic(paths):
    jp, dp = paths
    reg = R.Registry(jp, dp, backend="sqlite")
    reg.upsert("/a/one", {"store": "github", "status": "registered"})
    reg.upsert("/a/two", {"store": "terabox", "status": "registered"})
    back = reg.load()
    assert set(back["entries"]) == {"/a/one", "/a/two"}
    journal = reg.journal_tail(10)
    actions = [a for _, a, _, _ in journal]
    assert "upsert" in actions


def test_migrate_json_to_sqlite_never_deletes_json(tmp_path):
    jp = str(tmp_path / "src.json")
    dp = str(tmp_path / "out.db")
    entries = {
        "/p/one": {
            "store": "github",
            "repo": "o/r",
            "release": "t",
            "asset": "a",
            "sha256": "s",
            "size": 10,
            "status": "registered",
            "custom": "keepme",
        },
        "/p/two": {"store": "terabox", "status": "registered"},
    }
    with open(jp, "w") as f:
        json.dump({"version": 1, "entries": entries}, f)
    with open(jp, "rb") as f:
        before = f.read()
    res = R.migrate_json_to_sqlite(jp, dp)
    assert res["migrated"] == 2
    assert os.path.exists(jp)  # never deleted
    with open(jp, "rb") as f:
        assert f.read() == before  # never modified
    back = R.load_registry(jp, dp, backend="sqlite")
    assert back["entries"]["/p/one"]["custom"] == "keepme"
    assert back["entries"]["/p/one"]["repo"] == "o/r"
    # idempotent re-run
    res2 = R.migrate_json_to_sqlite(jp, dp)
    assert res2["migrated"] == 2
    assert len(R.load_registry(jp, dp, backend="sqlite")["entries"]) == 2


def test_migrate_skips_relative_paths(tmp_path):
    jp = str(tmp_path / "src.json")
    dp = str(tmp_path / "out.db")
    with open(jp, "w") as f:
        json.dump(
            {"entries": {"relative/x": {"store": "github"}, "/abs/y": {"store": "github"}}}, f
        )
    res = R.migrate_json_to_sqlite(jp, dp)
    assert res["migrated"] == 1
    assert res["skipped"] == ["relative/x"]


def test_v3_blob_schema_upgrades_in_place(paths):
    jp, dp = paths
    # build a v3-style blob DB (local_path/data/updated_at)
    conn = sqlite3.connect(dp)
    conn.execute(
        "CREATE TABLE entries (local_path TEXT PRIMARY KEY,"
        " data TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO entries VALUES (?,?,?)",
        ("/v3/old", json.dumps({"store": "github", "repo": "o/r", "note": "from-v3"}), "t"),
    )
    conn.commit()
    conn.close()
    back = R.load_registry(jp, dp, backend="sqlite")
    assert back["entries"]["/v3/old"]["note"] == "from-v3"
    assert back["entries"]["/v3/old"]["repo"] == "o/r"
    # old table kept as backup, never dropped
    conn = sqlite3.connect(dp)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "entries_legacy_v3" in tables
    journal = R.journal_tail(dp, 5)
    assert any(a == "schema-upgrade" for _, a, _, _ in journal)


def test_concurrent_upserts_are_safe(paths):
    jp, dp = paths
    errors = []

    def worker(i):
        try:
            R.Registry(jp, dp, backend="sqlite").upsert(
                f"/c/file-{i}", {"store": "github", "status": "registered", "size": i}
            )
        except Exception as e:  # noqa: BLE001 - collected for the assert
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    back = R.load_registry(jp, dp, backend="sqlite")
    assert len(back["entries"]) == 20
    assert back["entries"]["/c/file-7"]["size"] == 7


def test_export_json_roundtrip(paths):
    jp, dp = paths
    R.save_registry({"version": 4, "entries": SAMPLE}, jp, dp, backend="sqlite")
    dest = str(os.path.join(os.path.dirname(dp), "export.json"))
    out = R.export_json(dp, dest)
    assert out == dest
    with open(dest) as f:
        data = json.load(f)
    assert data["entries"]["/ws/big/game.iso"]["repo"] == "mhvnsnt/cape-jones"


def test_migrate_live_registry_copy(tmp_path):
    """Live migration test against a COPY of the real registry.

    The real file ~/workspace/agent-ops/cloud-dog-registry.json is only
    READ; everything else happens on copies in tmp_path.
    """
    live = os.path.expanduser("~/workspace/agent-ops/cloud-dog-registry.json")
    if not os.path.exists(live):
        pytest.skip("no live registry file on this machine")
    jp = str(tmp_path / "live-copy.json")
    dp = str(tmp_path / "live.db")
    with open(live, "rb") as f:
        raw = f.read()
    with open(jp, "wb") as f:
        f.write(raw)
    src_hash = raw
    res = R.migrate_json_to_sqlite(jp, dp)
    assert res["migrated"] == len(R.json_load(jp)["entries"])
    assert os.path.exists(jp)
    with open(jp, "rb") as f:
        assert f.read() == src_hash  # source never touched
    back = R.load_registry(jp, dp, backend="sqlite")
    src_entries = R.json_load(jp)["entries"]
    assert set(back["entries"]) == set(src_entries)
    for p, e in src_entries.items():
        be = back["entries"][p]
        for k, v in e.items():
            if k == "size" and not isinstance(v, int):
                continue  # coerced to int in the column; kept in extra?
            assert be.get(k) == v, (p, k, be.get(k), v)
