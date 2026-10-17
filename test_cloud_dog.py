"""Tests for cloud_dog.py — run with: python3 -m pytest test_cloud_dog.py -v
or: python3 test_cloud_dog.py (runs a smoke subset without pytest)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_dog


def test_sacred_paths():
    assert cloud_dog.is_sacred("/tmp/cj-canon/anything.txt")
    assert cloud_dog.is_sacred("/home/hatch/workspace/agent-ops/checkpoints/x.json")
    assert cloud_dog.is_sacred(os.path.expanduser("~/.ssh/id_ed25519"))
    assert not cloud_dog.is_sacred("/home/hatch/workspace/some-work/file.txt")


def test_inflight_suffix():
    inflight, _ = cloud_dog.is_inflight("/tmp/download.part")
    assert inflight
    inflight, _ = cloud_dog.is_inflight("/tmp/file.aria2")
    assert inflight


def test_inflight_fresh_file():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
        path = f.name
        f.write(b"x" * 100)
    try:
        inflight, reason = cloud_dog.is_inflight(path)
        assert inflight, "freshly-written file must be treated as in-flight"
    finally:
        os.unlink(path)


def test_human():
    assert cloud_dog.human(512) == "512.0B"
    assert cloud_dog.human(1536) == "1.5KB"
    assert cloud_dog.human(104857600) == "100.0MB"


def test_sha256_file():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        path = f.name
        f.write(b"cloud-dog-test")
    try:
        import hashlib
        assert cloud_dog.sha256_file(path) == hashlib.sha256(b"cloud-dog-test").hexdigest()
    finally:
        os.unlink(path)


def test_registry_roundtrip(tmp_path=None):
    # register + load must preserve the entry (uses real registry paths;
    # cleans up after itself). Backs up BOTH the legacy JSON and the
    # sqlite DB so the production registry is never polluted.
    import json
    reg_path = cloud_dog.REGISTRY_PATH
    db_path = cloud_dog.REGISTRY_DB_PATH
    backups = []
    for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            bak = p + ".test-bak"
            os.replace(p, bak)
            backups.append((p, bak))
    # keep test register events out of the production audit journal
    audit_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    audit_tmp.close()
    old_audit = _patch(cloud_dog, "AUDIT_PATH", audit_tmp.name)
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
            path = f.name
            f.write(b"data")
        try:
            rc = cloud_dog.cmd_register(type("A", (), {
                "file": path, "store": "github", "repo": "o/r",
                "release": "t", "asset": "a", "sha256": "",
                "size": 0, "note": "test",
            })())
            assert rc == 0
            reg = cloud_dog.load_registry()
            assert path in reg["entries"]
            assert reg["entries"][path]["sha256"] != ""
        finally:
            os.unlink(path)
    finally:
        _patch(cloud_dog, "AUDIT_PATH", old_audit)
        if os.path.exists(audit_tmp.name):
            os.unlink(audit_tmp.name)
        for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm",
                  db_path + ".test-bak-wal", db_path + ".test-bak-shm"):
            if os.path.exists(p):
                os.unlink(p)
        for p, bak in backups:
            if os.path.exists(bak):
                os.replace(bak, p)


def test_registry_sqlite_migration():
    # legacy JSON entries migrate into sqlite exactly once
    import json
    db_path = cloud_dog.REGISTRY_DB_PATH
    reg_path = cloud_dog.REGISTRY_PATH
    backups = []
    for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            bak = p + ".test-bak"
            os.replace(p, bak)
            backups.append((p, bak))
    try:
        legacy = {"version": 1, "entries": {
            "/tmp/fake-a": {"store": "github", "status": "registered"},
            "/tmp/fake-b": {"store": "terabox", "status": "reaped"},
        }}
        with open(reg_path, "w") as f:
            json.dump(legacy, f)
        reg = cloud_dog.load_registry()  # triggers migration
        assert "/tmp/fake-a" in reg["entries"]
        assert reg["entries"]["/tmp/fake-b"]["store"] == "terabox"
        # second load must not duplicate or wipe
        reg2 = cloud_dog.load_registry()
        assert len(reg2["entries"]) == 2
        # sqlite file now exists and holds the rows
        import sqlite3
        conn = sqlite3.connect(db_path)
        n = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        conn.close()
        assert n == 2
    finally:
        for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)
        for p, bak in backups:
            if os.path.exists(bak):
                os.replace(bak, p)


def test_registry_export_json(tmp_path):
    # export reproduces the legacy JSON shape from sqlite
    import json
    db_path = cloud_dog.REGISTRY_DB_PATH
    reg_path = cloud_dog.REGISTRY_PATH
    backups = []
    for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            bak = p + ".test-bak"
            os.replace(p, bak)
            backups.append((p, bak))
    try:
        cloud_dog.save_registry({"version": 3, "entries": {
            "/tmp/x": {"store": "github", "status": "registered"}}})
        dest = str(tmp_path / "export.json")
        out = cloud_dog.registry_export_json(dest)
        assert out == dest
        with open(dest) as f:
            data = json.load(f)
        assert "/tmp/x" in data["entries"]
    finally:
        for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)
        for p, bak in backups:
            if os.path.exists(bak):
                os.replace(bak, p)


if __name__ == "__main__":
    # smoke subset without pytest
    test_sacred_paths()
    test_inflight_suffix()
    test_inflight_fresh_file()
    test_human()
    test_sha256_file()
    print("smoke tests passed")


# ------------------------------------------------------- v2 feature tests
import io as _io
import json as _json


def _patch(obj, name, value):
    """Save/restore a module attribute (works with and without pytest)."""
    old = getattr(obj, name)
    setattr(obj, name, value)
    return old


def test_audit_log_writes_jsonl(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "audit.jsonl")
    old = _patch(cloud_dog, "AUDIT_PATH", p)
    try:
        ev = cloud_dog.audit_log("test_event", path="/x", ok=True)
        assert ev["action"] == "test_event" and ev["ok"] is True
        with open(p) as f:
            line = f.readline()
        back = _json.loads(line)
        assert back["action"] == "test_event" and "ts" in back
    finally:
        _patch(cloud_dog, "AUDIT_PATH", old)


def test_iter_file_parts_exact():
    data = b"abcdefghij" * 50  # 500 bytes
    parts = list(cloud_dog.iter_file_parts(_io.BytesIO(data), 200))
    assert [len(b) for _, b in parts] == [200, 200, 100]
    assert b"".join(b for _, b in parts) == data
    assert [i for i, _ in parts] == [0, 1, 2]


def test_iter_file_parts_empty():
    assert list(cloud_dog.iter_file_parts(_io.BytesIO(b""), 100)) == []


def test_multipart_encode_shape():
    body, ctype = cloud_dog.multipart_encode(
        {"title": "T"}, {"file": ("a.zip", b"DATA", "application/zip")})
    assert ctype.startswith("multipart/form-data; boundary=")
    assert b'name="title"' in body and b"\r\n\r\nT\r\n" in body
    assert b'filename="a.zip"' in body and b"DATA" in body
    assert body.endswith(b"--\r\n")


def test_terabox_upload_no_creds():
    old = _patch(cloud_dog, "terabox_creds_present", lambda: False)
    try:
        ok, detail = cloud_dog.terabox_upload("/tmp/x", "/remote")
        assert ok is False and "creds" in detail
    finally:
        _patch(cloud_dog, "terabox_creds_present", old)


def test_terabox_list_parses_ls_output():
    def fake_run(cmd, timeout=60):
        out = "f        12345 photo.png\nd         4096 subdir\nf       999 a b.bin\n"
        return 0, out, ""
    old_run = _patch(cloud_dog, "run", fake_run)
    old_creds = _patch(cloud_dog, "terabox_creds_present", lambda: True)
    try:
        ok, entries = cloud_dog.terabox_list("/dir")
        assert ok is True
        assert entries[0] == {"isdir": False, "size": 12345, "name": "photo.png"}
        assert entries[1]["isdir"] is True
        assert entries[2]["name"] == "a b.bin"  # spaces survive
    finally:
        _patch(cloud_dog, "run", old_run)
        _patch(cloud_dog, "terabox_creds_present", old_creds)


def test_verify_terabox_entry_size_mismatch():
    def fake_list(remote_dir):
        return True, [{"isdir": False, "size": 100, "name": "f.bin"}]
    old = _patch(cloud_dog, "terabox_list", fake_list)
    try:
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "f.bin", "size": 999}, "/tmp/f.bin")
        assert ok is False and "size mismatch" in detail
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "f.bin", "size": 100}, "/tmp/f.bin")
        assert ok is True and "verified" in detail
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "missing.bin"}, "/tmp/f.bin")
        assert ok is False and "not found" in detail
    finally:
        _patch(cloud_dog, "terabox_list", old)


def test_kaggle_request_no_creds():
    old = _patch(cloud_dog, "kaggle_creds", lambda: (None, None))
    try:
        ok, detail = cloud_dog.kaggle_request("GET", "/datasets/list")
        assert ok is False and "kaggle.json" in detail
    finally:
        _patch(cloud_dog, "kaggle_creds", old)


def test_kaggle_request_sends_basic_auth():
    import urllib.request
    seen = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, timeout=120):
        seen["auth"] = req.get_header("Authorization")
        seen["url"] = req.full_url
        return FakeResp()

    old_creds = _patch(cloud_dog, "kaggle_creds", lambda: ("marsfmmg", "KEY123"))
    old_open = _patch(urllib.request, "urlopen", fake_urlopen)
    try:
        ok, payload = cloud_dog.kaggle_request("GET", "/datasets/list")
        assert ok is True and payload == {"ok": True}
        import base64
        expect = "Basic " + base64.b64encode(b"marsfmmg:KEY123").decode()
        assert seen["auth"] == expect
        assert seen["url"] == "https://www.kaggle.com/api/v1/datasets/list"
    finally:
        _patch(cloud_dog, "kaggle_creds", old_creds)
        _patch(urllib.request, "urlopen", old_open)


def test_verify_kaggle_entry_found_and_sized():
    def fake_view(owner_slug):
        return True, {"files": [
            {"ref": "u/ds/a.bin", "name": "a.bin", "totalBytes": 500},
        ]}
    old = _patch(cloud_dog, "kaggle_dataset_view", fake_view)
    try:
        e = {"dataset": "u/ds", "asset": "a.bin", "size": 500}
        ok, detail = cloud_dog.verify_kaggle_entry(e, "/tmp/a.bin")
        assert ok is True and "verified" in detail
        e2 = {"dataset": "u/ds", "asset": "a.bin", "size": 501}
        ok, _ = cloud_dog.verify_kaggle_entry(e2, "/tmp/a.bin")
        assert ok is False
        e3 = {"dataset": "u/ds", "asset": "nope.bin"}
        ok, detail = cloud_dog.verify_kaggle_entry(e3, "/tmp/a.bin")
        assert ok is False and "not found" in detail
    finally:
        _patch(cloud_dog, "kaggle_dataset_view", old)


def test_verify_entry_dispatches_on_store():
    calls = []

    def fake_gh(repo, release, asset, size=0, sha=""):
        calls.append("github")
        return True, "gh-ok"

    def fake_tb(entry, path):
        calls.append("terabox")
        return True, "tb-ok"

    def fake_kg(entry, path):
        calls.append("kaggle")
        return True, "kg-ok"

    o1 = _patch(cloud_dog, "verify_github_asset", fake_gh)
    o2 = _patch(cloud_dog, "verify_terabox_entry", fake_tb)
    o3 = _patch(cloud_dog, "verify_kaggle_entry", fake_kg)
    try:
        assert cloud_dog.verify_entry({"store": "github"}, "/p")[0]
        assert cloud_dog.verify_entry({"store": "terabox"}, "/p")[0]
        assert cloud_dog.verify_entry({"store": "kaggle"}, "/p")[0]
        assert calls == ["github", "terabox", "kaggle"]
        ok, detail = cloud_dog.verify_entry({"store": "nope"}, "/p")
        assert ok is False and "unknown store" in detail
    finally:
        _patch(cloud_dog, "verify_github_asset", o1)
        _patch(cloud_dog, "verify_terabox_entry", o2)
        _patch(cloud_dog, "verify_kaggle_entry", o3)


def test_upload_to_store_unknown():
    ok, detail = cloud_dog.upload_to_store("nope", "/tmp/x", {})
    assert ok is False and "unknown store" in detail


def test_daemon_refuses_second_instance(tmp_path=None):
    import tempfile
    d = tempfile.mkdtemp()
    pf = os.path.join(d, "cd.pid")
    with open(pf, "w") as f:
        f.write(str(os.getpid()))  # this process IS alive
    o1 = _patch(cloud_dog, "log_action", lambda *a, **k: None)
    o2 = _patch(cloud_dog, "audit_log", lambda *a, **k: {})
    try:
        args = type("A", (), {"pidfile": pf, "interval": 600, "apply": False})()
        rc = cloud_dog.cmd_daemon(args)
        assert rc == 1
    finally:
        _patch(cloud_dog, "log_action", o1)
        _patch(cloud_dog, "audit_log", o2)


def test_daemon_stale_pidfile_taken_over(tmp_path=None):
    import tempfile, threading
    d = tempfile.mkdtemp()
    pf = os.path.join(d, "cd.pid")
    with open(pf, "w") as f:
        f.write("999999999")  # no such pid -> stale
    stopped = []

    real_sleep = __import__("time").sleep

    def fast_sleep(s):
        stopped.append(s)
        raise KeyboardInterrupt()

    import time as _t
    old_sleep = _patch(_t, "sleep", fast_sleep)
    old_enforce = _patch(cloud_dog, "cmd_enforce",
                         lambda a: (_ for _ in ()).throw(KeyboardInterrupt()))
    o1 = _patch(cloud_dog, "log_action", lambda *a, **k: None)
    o2 = _patch(cloud_dog, "audit_log", lambda *a, **k: {})
    args = type("A", (), {"pidfile": pf, "interval": 600, "apply": False})()
    try:
        rc = cloud_dog.cmd_daemon(args)
    except KeyboardInterrupt:
        rc = 130
    finally:
        _patch(_t, "sleep", old_sleep)
        _patch(cloud_dog, "cmd_enforce", old_enforce)
        _patch(cloud_dog, "log_action", o1)
        _patch(cloud_dog, "audit_log", o2)
    assert not os.path.exists(pf), "pidfile must be removed on exit"


def test_enforce_uses_v2_reaper():
    """enforce must reap via the v2 dispatcher (all stores), and must not
    crash when args has no .path (the enforce parser never defines --path)."""
    calls = []

    def fake_scan():
        return {"over_ceiling": True}

    def fake_reap_v2(args):
        calls.append("v2")
        # must tolerate missing .path
        _ = getattr(args, "path", "")
        return 0

    o1 = _patch(cloud_dog, "cmd_scan", fake_scan)
    o2 = _patch(cloud_dog, "cmd_reap_v2", fake_reap_v2)
    o3 = _patch(cloud_dog, "disk_usage_percent", lambda p="/home/hatch": (80.0, 1, 2))
    o4 = _patch(cloud_dog, "log_action", lambda *a, **k: None)
    try:
        args = type("A", (), {"apply": True})()  # NO .path attribute
        rc = cloud_dog.cmd_enforce(args)
        assert rc == 0
        assert calls == ["v2"], "enforce must dispatch to cmd_reap_v2"
    finally:
        _patch(cloud_dog, "cmd_scan", o1)
        _patch(cloud_dog, "cmd_reap_v2", o2)
        _patch(cloud_dog, "disk_usage_percent", o3)
        _patch(cloud_dog, "log_action", o4)


def test_reap_v2_tolerates_missing_path_arg():
    """cmd_reap_v2 directly with an args object lacking .path (enforce path)."""
    o1 = _patch(cloud_dog, "reap_candidates", lambda root=None: iter([]))
    try:
        args = type("A", (), {"apply": False})()
        rc = cloud_dog.cmd_reap_v2(args)
        assert rc == 0
    finally:
        _patch(cloud_dog, "reap_candidates", o1)


# ------------------------------------------------- policy-gate wiring tests
def _policy_test_setup():
    """Back up the real registry and isolate the audit journal."""
    import shutil
    reg_path = cloud_dog.REGISTRY_PATH
    backup = None
    if os.path.exists(reg_path):
        backup = reg_path + ".test-bak"
        os.replace(reg_path, backup)
    d = tempfile.mkdtemp(prefix="cdtest-",
                        dir=os.path.expanduser("~/workspace"))
    old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(d, "audit.jsonl"))

    def cleanup():
        _patch(cloud_dog, "AUDIT_PATH", old_audit)
        shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(reg_path):
            os.unlink(reg_path)
        if backup and os.path.exists(backup):
            os.replace(backup, reg_path)
    return d, cleanup


def _old_file(directory, name, content=b"data"):
    """Create a file with mtime old enough to pass the in-flight filter."""
    import time
    p = os.path.join(directory, name)
    with open(p, "wb") as f:
        f.write(content)
    old = time.time() - 600
    os.utime(p, (old, old))
    return p


def _register(path, store="terabox"):
    return cloud_dog.cmd_register(type("A", (), {
        "file": path, "store": store, "repo": "", "release": "",
        "asset": "", "sha256": "", "size": 0, "note": "test",
    })())


def _reap_dry(args):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cloud_dog.cmd_reap_v2(args)
    return rc, buf.getvalue()


def test_policy_gate_blocks_protected_game_file():
    """A .iso with a claimed store is BLOCKED by decide_delete (rule 3),
    before any live verify runs. The file must survive."""
    d, cleanup = _policy_test_setup()
    try:
        iso = _old_file(d, "game.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": True, "path": "", "owner_approved": False})())
        assert rc == 0
        assert "blocked-policy=1" in out, out
        assert "BLOCKED (policy)" in out, out
        assert "protected game file" in out, out
        assert os.path.exists(iso), "policy-blocked file must never be deleted"
    finally:
        cleanup()


def test_policy_gate_passes_ordinary_file_to_verify():
    """An ordinary file with a claimed store passes the gate and reaches
    live verification (fails locally without creds — no network)."""
    d, cleanup = _policy_test_setup()
    try:
        dat = _old_file(d, "ordinary.dat")
        assert _register(dat) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": False})())
        assert rc == 0
        assert "blocked-policy=0" in out, out
        assert "failed-verify=1" in out, out
        assert os.path.exists(dat)
    finally:
        cleanup()


def test_policy_gate_owner_approved_allows_game_file_past_gate():
    """With --owner-approved, a protected game file passes the POLICY gate
    (rule 3 satisfied) and proceeds to live verification."""
    d, cleanup = _policy_test_setup()
    try:
        iso = _old_file(d, "game2.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": True})())
        assert rc == 0
        assert "blocked-policy=0" in out, out
        assert os.path.exists(iso)
    finally:
        cleanup()


def test_policy_unavailable_fails_closed():
    """If the law engine cannot import, reap refuses to run at all."""
    import io, contextlib
    old = _patch(cloud_dog, "_POLICY_AVAILABLE", False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cloud_dog.cmd_reap_v2(type("A", (), {"apply": True})())
        assert rc == 1
        assert "fail-closed" in buf.getvalue()
    finally:
        _patch(cloud_dog, "_POLICY_AVAILABLE", old)
