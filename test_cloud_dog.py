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
    # legacy JSON entries migrate into sqlite exactly once.
    # (v4: the default backend is json; this test pins the sqlite path,
    #  so it selects the sqlite backend explicitly and restores it.)
    prev_backend = cloud_dog._REGISTRY_BACKEND
    cloud_dog.set_registry_backend("sqlite")
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
        cloud_dog.set_registry_backend(prev_backend)
        for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)
        for p, bak in backups:
            if os.path.exists(bak):
                os.replace(bak, p)


def test_registry_export_json(tmp_path):
    # export reproduces the legacy JSON shape from sqlite.
    # (v4: selects the sqlite backend explicitly; default is json.)
    prev_backend = cloud_dog._REGISTRY_BACKEND
    cloud_dog.set_registry_backend("sqlite")
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
        cloud_dog.set_registry_backend(prev_backend)
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
    # Owner decision 2026-10-19: name+size is REMOVED as a verification
    # basis. Even a perfectly matching live listing must NOT pass —
    # verification fails closed because no byte-level re-download exists.
    def fake_list(remote_dir):
        return True, [{"isdir": False, "size": 100, "name": "f.bin"}]
    old = _patch(cloud_dog, "terabox_list", fake_list)
    try:
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "f.bin", "size": 999}, "/tmp/f.bin")
        assert ok is False and "fail closed" in detail
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "f.bin", "size": 100}, "/tmp/f.bin")
        assert ok is False and "fail closed" in detail
        ok, detail = cloud_dog.verify_terabox_entry(
            {"remote_dir": "/d", "asset": "missing.bin"}, "/tmp/f.bin")
        assert ok is False and "fail closed" in detail
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
    # Owner decision 2026-10-19: the dataset file list (name+size) is
    # metadata, not proof. Even a perfectly matching listing must NOT
    # pass — verification fails closed until byte-level re-download is
    # wired for kaggle.
    def fake_list(owner_slug):
        return True, {"datasetFiles": [
            {"name": "a.bin", "totalBytes": 500},
        ]}
    old = _patch(cloud_dog, "kaggle_dataset_list_files", fake_list)
    try:
        e = {"dataset": "u/ds", "asset": "a.bin", "size": 500}
        ok, detail = cloud_dog.verify_kaggle_entry(e, "/tmp/a.bin")
        assert ok is False and "fail closed" in detail
        e2 = {"dataset": "u/ds", "asset": "a.bin", "size": 501}
        ok, detail = cloud_dog.verify_kaggle_entry(e2, "/tmp/a.bin")
        assert ok is False and "fail closed" in detail
        e3 = {"dataset": "u/ds", "asset": "nope.bin"}
        ok, detail = cloud_dog.verify_kaggle_entry(e3, "/tmp/a.bin")
        assert ok is False and "fail closed" in detail
    finally:
        _patch(cloud_dog, "kaggle_dataset_list_files", old)


def test_verify_kaggle_entry_live_readonly():
    """Live-shape check: with the decision in force, even a real dataset
    entry cannot pass without a byte-level re-download (fail closed)."""
    e = {"dataset": "marsfmmg/cj-living-room-setup",
         "asset": "dvd_stack_ref.png", "size": 4848}
    ok, detail = cloud_dog.verify_kaggle_entry(e, "/tmp/dvd_stack_ref.png")
    assert ok is False and "fail closed" in detail, detail
    e2 = {"dataset": "marsfmmg/cj-living-room-setup",
          "asset": "does-not-exist.bin"}
    ok, detail = cloud_dog.verify_kaggle_entry(e2, "/tmp/x.bin")
    assert ok is False and "fail closed" in detail


def test_kaggle_dataset_upload_new_no_cli():
    old_which = _patch(cloud_dog.shutil, "which", lambda *a, **k: None)
    old_isfile = _patch(cloud_dog.os.path, "isfile", lambda p: False)
    try:
        ok, detail = cloud_dog.kaggle_dataset_upload_new(
            "u", "someslug", "Some Title", "/tmp/x.zip")
        assert ok is False and "kaggle CLI not found" in detail
    finally:
        _patch(cloud_dog.shutil, "which", old_which)
        _patch(cloud_dog.os.path, "isfile", old_isfile)


def test_kaggle_dataset_upload_new_validates():
    old = _patch(cloud_dog.shutil, "which", lambda *a, **k: "/usr/bin/kaggle")
    try:
        ok, detail = cloud_dog.kaggle_dataset_upload_new(
            "u", "abc", "Some Title", "/tmp/x.zip")
        assert ok is False and "6-50 chars" in detail
        ok, detail = cloud_dog.kaggle_dataset_upload_new(
            "u", "validslug", "x", "/tmp/x.zip")
        assert ok is False and "6-50 chars" in detail
        ok, detail = cloud_dog.kaggle_dataset_upload_new(
            "u", "validslug", "Valid Title", "/tmp/does-not-exist.zip")
        assert ok is False and "not found" in detail
    finally:
        _patch(cloud_dog.shutil, "which", old)


def test_kaggle_dataset_upload_new_cli_success():
    import tempfile
    old_which = _patch(cloud_dog.shutil, "which", lambda *a, **k: "/usr/bin/kaggle")
    old_run = _patch(cloud_dog, "run",
                     lambda cmd, timeout=60: (0, "Dataset URL: ...", ""))
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as f:
            f.write(b"PK fake")
            zp = f.name
        try:
            ok, detail = cloud_dog.kaggle_dataset_upload_new(
                "marsfmmg", "testslug123", "Test Title Here", zp)
            assert ok is True and "marsfmmg/testslug123" in detail
        finally:
            os.unlink(zp)
    finally:
        _patch(cloud_dog.shutil, "which", old_which)
        _patch(cloud_dog, "run", old_run)


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
    """A .iso with a claimed store is BLOCKED by decide_delete (game-file
    strict bar: retrievability_proven missing), before any live verify
    runs. The file must survive."""
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
        assert "retrievability_proven" in out, out
        assert os.path.exists(iso), "policy-blocked file must never be deleted"
    finally:
        cleanup()


def _cold_big_file(directory, name, size_mb=12):
    """Sparse file >= 10MB with mtime old enough to be STORE_AND_CLEAR."""
    import time
    p = os.path.join(directory, name)
    with open(p, "wb") as f:
        f.truncate(size_mb * 1024 * 1024)
    old = time.time() - 7200
    os.utime(p, (old, old))
    return p


def test_policy_gate_passes_ordinary_file_to_verify():
    """A cold >= 10MB ordinary file evaluates STORE_AND_CLEAR, passes the
    gate, and reaches live verification (fails locally without creds —
    no network)."""
    d, cleanup = _policy_test_setup()
    try:
        dat = _cold_big_file(d, "ordinary.dat")
        assert _register(dat) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": False})())
        assert rc == 0
        assert "blocked-policy=0" in out, out
        assert "failed-verify=1" in out, out
        assert os.path.exists(dat)
    finally:
        cleanup()


class _FakeProof:
    """Stub stand-in for retrievability.ProofResult in reap wiring tests."""

    def __init__(self, proven, reason=""):
        self._d = {"proven": proven, "bytes_fetched": 123,
                   "hash_matched": proven, "size_matched": proven,
                   "expected_sha256": "e" * 64, "actual_sha256": "e" * 64,
                   "expected_size": 123, "locator": "o/r@tag/a",
                   "ts": "2026-10-20T00:00:00+00:00", "attempts": 1,
                   "reason": reason}

    def to_dict(self):
        return dict(self._d)


def _audit_events():
    import json
    with open(cloud_dog.AUDIT_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def test_policy_gate_owner_approved_allows_game_file_past_gate():
    """With --owner-approved AND --retrievability-proven (which now RUNS
    the pull-back prover instead of merely asserting the flag), a
    protected game file passes the POLICY gate (strict bar satisfied)
    and proceeds to live verification. The prover's verdict — not the
    flag — decides, and the proof is recorded in the audit journal."""
    d, cleanup = _policy_test_setup()
    old = _patch(cloud_dog, "_run_retrievability_proof",
                 lambda entry: _FakeProof(True, "pull-back fetched and hash-verified"))
    try:
        iso = _old_file(d, "game2.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": True,
            "retrievability_proven": True})())
        assert rc == 0
        assert "blocked-policy=0" in out, out
        proofs = [e for e in _audit_events()
                  if e.get("action") == "reap_retrievability_proof"]
        assert len(proofs) == 1, [e.get("action") for e in _audit_events()]
        assert proofs[0]["proven"] is True
        assert proofs[0]["bytes_fetched"] == 123
        assert proofs[0]["hash_matched"] is True
        assert proofs[0]["proof_ts"] == "2026-10-20T00:00:00+00:00"
        assert os.path.exists(iso)
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)
        cleanup()


def test_policy_gate_game_file_blocked_when_pullback_proof_fails():
    """The flag no longer asserts proof: when the prover runs and FAILS,
    the game-file strict bar is unsatisfied (fail closed), the reap is
    blocked, the block names the proof failure, and the file survives."""
    d, cleanup = _policy_test_setup()
    old = _patch(cloud_dog, "_run_retrievability_proof",
                 lambda entry: _FakeProof(False, "sha256 mismatch after pull-back"))
    try:
        iso = _old_file(d, "game4.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": True,
            "retrievability_proven": True})())
        assert rc == 0
        assert "blocked-policy=1" in out, out
        assert "pull-back proof FAILED" in out, out
        assert "sha256 mismatch" in out, out
        proofs = [e for e in _audit_events()
                  if e.get("action") == "reap_retrievability_proof"]
        assert len(proofs) == 1
        assert proofs[0]["proven"] is False
        assert os.path.exists(iso), "proof-failed file must never be deleted"
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)
        cleanup()


def test_policy_gate_game_file_prover_unavailable_fails_closed():
    """When the prover module cannot run (returns None), a
    --retrievability-proven strict run still blocks (fail closed),
    naming the gap in the audit journal."""
    d, cleanup = _policy_test_setup()
    old = _patch(cloud_dog, "_run_retrievability_proof", lambda entry: None)
    try:
        iso = _old_file(d, "game5.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": True,
            "retrievability_proven": True})())
        assert rc == 0
        assert "blocked-policy=1" in out, out
        proofs = [e for e in _audit_events()
                  if e.get("action") == "reap_retrievability_proof"]
        assert len(proofs) == 1
        assert proofs[0]["proven"] is False
        assert "unavailable" in proofs[0]["detail"]
        assert os.path.exists(iso), "fail-closed file must never be deleted"
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)
        cleanup()


def test_policy_gate_game_file_blocked_without_retrievability_proven():
    """Owner approval alone no longer clears the game-file strict bar:
    retrievability_proven is required, and the block names it."""
    d, cleanup = _policy_test_setup()
    try:
        iso = _old_file(d, "game3.iso")
        assert _register(iso) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": True})())
        assert rc == 0
        assert "blocked-policy=1" in out, out
        assert "retrievability_proven" in out, out
        assert os.path.exists(iso), "strict-bar-blocked file must survive"
    finally:
        cleanup()


def test_evaluate_sacred_blocks_path_v2_would_pass():
    """V1's evaluate runs FIRST: a stale .downloading file (suffix V2 does
    not treat as transient, mtime old enough to pass V2's in-flight check)
    is blocked as evaluate(SACRED) even though every V2 check would pass."""
    d, cleanup = _policy_test_setup()
    try:
        stale = _old_file(d, "stale-download.downloading")
        assert _register(stale) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": False})())
        assert rc == 0
        assert "blocked-policy=1" in out, out
        assert "evaluate(SACRED)" in out, out
        assert os.path.exists(stale), "evaluate-SACRED file must survive"
    finally:
        cleanup()


def test_evaluate_keep_local_skips():
    """V1 evaluate(KEEP_LOCAL) skips the candidate before decide_delete:
    a small file modified 10 min ago (passes V2's in-flight check) is
    'hot' under V1 and is not reap-eligible."""
    d, cleanup = _policy_test_setup()
    try:
        hot = _old_file(d, "hot.dat")
        assert _register(hot) == 0
        rc, out = _reap_dry(type("A", (), {
            "apply": False, "path": "", "owner_approved": False})())
        assert rc == 0
        assert "skipped-evaluate=1" in out, out
        assert "SKIP (evaluate)" in out, out
        assert "keep_local" in out, out
        assert os.path.exists(hot), "evaluate-skipped file must survive"
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


def test_evaluate_unavailable_fails_closed():
    """If the V1 front decider cannot import, reap refuses to run at all —
    it must not silently lose the evaluate guard."""
    import io, contextlib
    old = _patch(cloud_dog, "_EVALUATE_AVAILABLE", False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cloud_dog.cmd_reap_v2(type("A", (), {"apply": True})())
        assert rc == 1
        assert "fail-closed" in buf.getvalue()
    finally:
        _patch(cloud_dog, "_EVALUATE_AVAILABLE", old)


# ---------------------------------------------------- dust scan wiring tests
def test_parse_dust_size():
    assert cloud_dog._parse_dust_size("2.1M") == int(2.1 * 1024 ** 2)
    assert cloud_dog._parse_dust_size("272K") == 272 * 1024
    assert cloud_dog._parse_dust_size("512B") == 512
    assert cloud_dog._parse_dust_size("1.5G") == int(1.5 * 1024 ** 3)
    assert cloud_dog._parse_dust_size("nonsense") == 0
    assert cloud_dog._parse_dust_size("") == 0


def _fake_dust_script(directory, payload):
    """Write a fake `dust` executable that prints payload for -j."""
    import stat
    p = os.path.join(directory, "dust")
    with open(p, "w") as f:
        f.write("#!/bin/sh\nprintf '%s' '" + payload.replace("'", "'\"'\"'") + "'\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def test_dust_largest_parses_json():
    import json, tempfile
    d = tempfile.mkdtemp(prefix="cdtest-", dir=os.path.expanduser("~/workspace"))
    try:
        payload = json.dumps({"size": "3.0M", "name": "/root",
                              "children": [
                                  {"size": "2.0M", "name": "/root/aaa", "children": []},
                                  {"size": "1.0M", "name": "/root/bbb", "children": []},
                              ]})
        _fake_dust_script(d, payload)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            out = cloud_dog._dust_largest("/root")
        finally:
            os.environ["PATH"] = old_path
        assert out is not None
        by_name = dict(out)
        assert by_name["aaa"] == 2 * 1024 ** 2
        assert by_name["bbb"] == 1 * 1024 ** 2
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_dust_largest_bad_output_falls_back():
    import tempfile
    d = tempfile.mkdtemp(prefix="cdtest-", dir=os.path.expanduser("~/workspace"))
    try:
        _fake_dust_script(d, "this is not json{{{")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            assert cloud_dog._dust_largest("/root") is None
        finally:
            os.environ["PATH"] = old_path
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_scan_uses_dust_when_present():
    import json, tempfile
    d = tempfile.mkdtemp(prefix="cdtest-", dir=os.path.expanduser("~/workspace"))
    try:
        payload = json.dumps({"size": "9.9M", "name": "ws", "children": [
            {"size": "9.9M", "name": "ws/fakedir", "children": []}]})
        _fake_dust_script(d, payload)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            report = cloud_dog.cmd_scan(as_json=True)
        finally:
            os.environ["PATH"] = old_path
        names = [i["name"] for i in report["largest"]]
        assert "fakedir" in names, report["largest"]
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def test_scan_falls_back_without_dust():
    old = _patch(cloud_dog, "_dust_binary", lambda: None)
    try:
        report = cloud_dog.cmd_scan(as_json=True)
        assert report["largest"], "stdlib fallback must still list entries"
        assert all("bytes" in i and i["bytes"] >= 0 for i in report["largest"])
    finally:
        _patch(cloud_dog, "_dust_binary", old)


# -------------------------------------------------- fclones dedup wiring tests
def _fake_fclones_script(directory, groups):
    """Write a fake `fclones` that prints canned group JSON."""
    import json, stat
    payload = json.dumps({"header": {"version": "0.34.0",
                                     "stats": {"group_count": len(groups)}},
                          "groups": groups})
    p = os.path.join(directory, "fclones")
    with open(p, "w") as f:
        f.write("#!/bin/sh\nprintf '%s' '" + payload.replace("'", "'\"'\"'") + "'\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def _dedup_test_dir():
    import shutil
    d = tempfile.mkdtemp(prefix="cddedup-", dir=os.path.expanduser("~/workspace"))
    return d


def _with_fake_fclones(d, groups):
    _fake_fclones_script(d, groups)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = d + os.pathsep + old_path
    return old_path


def test_fclones_groups_parses_json():
    import shutil
    d = _dedup_test_dir()
    try:
        old_path = _with_fake_fclones(d, [
            {"file_len": 6, "file_hash": "abc",
             "files": ["/a/f1.txt", "/b/f2.txt"]}])
        try:
            groups, err = cloud_dog._fclones_groups(["/root"], [])
        finally:
            os.environ["PATH"] = old_path
        assert err is None
        assert len(groups) == 1
        assert groups[0]["files"] == ["/a/f1.txt", "/b/f2.txt"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_fclones_groups_bad_json_errors():
    import shutil, stat
    d = _dedup_test_dir()
    try:
        p = os.path.join(d, "fclones")
        with open(p, "w") as f:
            f.write("#!/bin/sh\necho 'not json{{{' \n")
        os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = d + os.pathsep + old_path
        try:
            groups, err = cloud_dog._fclones_groups(["/root"], [])
        finally:
            os.environ["PATH"] = old_path
        assert groups is None
        assert "JSON" in err
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_dry_run_lists_duplicates():
    import shutil, io, contextlib
    d = _dedup_test_dir()
    try:
        f1 = os.path.join(d, "f1.txt")
        f2 = os.path.join(d, "f2.txt")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("same-content")
        old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(d, "a.jsonl"))
        old_path = _with_fake_fclones(d, [
            {"file_len": 12, "file_hash": "abc", "files": [f1, f2]}])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cloud_dog.cmd_dedup(type("A", (), {
                    "apply": False, "path": d, "owner_approved": False,
                    "exclude": []})())
            out = buf.getvalue()
        finally:
            os.environ["PATH"] = old_path
            _patch(cloud_dog, "AUDIT_PATH", old_audit)
        assert rc == 0
        assert "WOULD-DEDUP" in out, out
        assert "reaped=1" in out, out
        assert os.path.exists(f1) and os.path.exists(f2), "dry-run deletes nothing"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_apply_deletes_duplicate_keeps_one():
    import shutil, io, contextlib
    d = _dedup_test_dir()
    try:
        f1 = os.path.join(d, "a1.txt")
        f2 = os.path.join(d, "a2.txt")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("identical-bytes")
        old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(d, "a.jsonl"))
        old_path = _with_fake_fclones(d, [
            {"file_len": 15, "file_hash": "abc", "files": [f1, f2]}])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cloud_dog.cmd_dedup(type("A", (), {
                    "apply": True, "path": d, "owner_approved": False,
                    "exclude": []})())
        finally:
            os.environ["PATH"] = old_path
            _patch(cloud_dog, "AUDIT_PATH", old_audit)
        assert rc == 0
        # sorted order: a1.txt kept, a2.txt deleted
        assert os.path.exists(f1), "kept copy must survive"
        assert not os.path.exists(f2), "duplicate must be deleted"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_skips_git_tree():
    import shutil, io, contextlib
    d = _dedup_test_dir()
    try:
        os.makedirs(os.path.join(d, ".git"))
        f1 = os.path.join(d, "g1.txt")
        f2 = os.path.join(d, "g2.txt")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("same")
        old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(d, "a.jsonl"))
        old_path = _with_fake_fclones(d, [
            {"file_len": 4, "file_hash": "abc", "files": [f1, f2]}])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cloud_dog.cmd_dedup(type("A", (), {
                    "apply": True, "path": d, "owner_approved": False,
                    "exclude": []})())
            out = buf.getvalue()
        finally:
            os.environ["PATH"] = old_path
            _patch(cloud_dog, "AUDIT_PATH", old_audit)
        assert rc == 0
        assert "git working tree" in out, out
        assert os.path.exists(f1) and os.path.exists(f2)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_blocks_game_file_without_approval():
    import shutil, io, contextlib
    d = _dedup_test_dir()
    try:
        f1 = os.path.join(d, "h1.iso")
        f2 = os.path.join(d, "h2.iso")
        for p in (f1, f2):
            with open(p, "w") as f:
                f.write("iso-bytes")
        old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(d, "a.jsonl"))
        old_path = _with_fake_fclones(d, [
            {"file_len": 9, "file_hash": "abc", "files": [f1, f2]}])
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = cloud_dog.cmd_dedup(type("A", (), {
                    "apply": True, "path": d, "owner_approved": False,
                    "exclude": []})())
            out = buf.getvalue()
        finally:
            os.environ["PATH"] = old_path
            _patch(cloud_dog, "AUDIT_PATH", old_audit)
        assert rc == 0
        assert "protected game file" in out, out
        assert os.path.exists(f1) and os.path.exists(f2)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dedup_fails_closed_without_policy():
    import io, contextlib
    old = _patch(cloud_dog, "_POLICY_AVAILABLE", False)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cloud_dog.cmd_dedup(type("A", (), {"apply": True})())
        assert rc == 1
        assert "fail-closed" in buf.getvalue()
    finally:
        _patch(cloud_dog, "_POLICY_AVAILABLE", old)


def test_dedup_no_binary_errors():
    old = _patch(cloud_dog, "_fclones_binary", lambda: None)
    try:
        rc = cloud_dog.cmd_dedup(type("A", (), {
            "apply": False, "path": "", "owner_approved": False, "exclude": []})())
        assert rc == 1
    finally:
        _patch(cloud_dog, "_fclones_binary", old)


# ------------------------------------------------------ restic backend tests
def _restic_test_env(password="test-rest1c-pw"):
    """Set RESTIC_PASSWORD for the test, returning the old value."""
    old = os.environ.get("RESTIC_PASSWORD")
    os.environ["RESTIC_PASSWORD"] = password
    return old


def _restic_restore_env(old):
    if old is None:
        os.environ.pop("RESTIC_PASSWORD", None)
    else:
        os.environ["RESTIC_PASSWORD"] = old


def test_restic_binary_found():
    assert cloud_dog._restic_binary() is not None


def test_restic_backup_and_verify_roundtrip():
    import shutil
    old_pw = _restic_test_env()
    d = tempfile.mkdtemp(prefix="cdrestic-")
    try:
        repo = os.path.join(d, "repo")
        f1 = os.path.join(d, "f1.txt")
        with open(f1, "w") as f:
            f.write("restic-roundtrip-bytes")
        ok, snap = cloud_dog.restic_backup(f1, repo)
        assert ok, f"backup failed: {snap}"
        assert len(snap) == 64, f"snapshot id should be 64 hex chars: {snap}"
        entry = {"restic_repo": repo, "snapshot_id": snap,
                 "local_path": f1}
        # Backup + snapshot are real, but verify FAILS CLOSED since the
        # owner decision (2026-10-19): a snapshot listing is metadata,
        # not a byte-level re-download proof.
        ok, detail = cloud_dog.restic_verify(entry, f1)
        assert not ok and "fail closed" in detail
    finally:
        _restic_restore_env(old_pw)
        shutil.rmtree(d, ignore_errors=True)


def test_restic_verify_missing_snapshot_fails():
    import shutil
    old_pw = _restic_test_env()
    d = tempfile.mkdtemp(prefix="cdrestic-")
    try:
        repo = os.path.join(d, "repo")
        f1 = os.path.join(d, "f1.txt")
        with open(f1, "w") as f:
            f.write("x")
        ok, snap = cloud_dog.restic_backup(f1, repo)
        assert ok
        entry = {"restic_repo": repo,
                 "snapshot_id": "0" * 64,
                 "local_path": f1}
        ok, detail = cloud_dog.restic_verify(entry, f1)
        assert not ok
        assert "fail closed" in detail
    finally:
        _restic_restore_env(old_pw)
        shutil.rmtree(d, ignore_errors=True)


def test_restic_verify_wrong_path_fails():
    import shutil
    old_pw = _restic_test_env()
    d = tempfile.mkdtemp(prefix="cdrestic-")
    try:
        repo = os.path.join(d, "repo")
        f1 = os.path.join(d, "f1.txt")
        with open(f1, "w") as f:
            f.write("x")
        ok, snap = cloud_dog.restic_backup(f1, repo)
        assert ok
        entry = {"restic_repo": repo, "snapshot_id": snap,
                 "local_path": f1}
        ok, detail = cloud_dog.restic_verify(entry, "/nope/other.txt")
        assert not ok
        assert "fail closed" in detail
    finally:
        _restic_restore_env(old_pw)
        shutil.rmtree(d, ignore_errors=True)


def test_restic_backup_needs_password():
    old_pw = _restic_test_env()
    _restic_restore_env(None)  # ensure unset
    try:
        ok, detail = cloud_dog.restic_backup("/tmp/x", "/tmp/r")
        assert not ok
        assert "RESTIC_PASSWORD" in detail
    finally:
        _restic_restore_env(old_pw)


def test_restic_verify_needs_password():
    old_pw = _restic_test_env()
    _restic_restore_env(None)
    try:
        ok, detail = cloud_dog.restic_verify(
            {"restic_repo": "/tmp/r", "snapshot_id": "a" * 64}, "/tmp/x")
        assert not ok
        assert "fail closed" in detail
    finally:
        _restic_restore_env(old_pw)


def test_restic_backup_missing_binary():
    old = cloud_dog._restic_binary
    cloud_dog._restic_binary = lambda: None
    try:
        ok, detail = cloud_dog.restic_backup("/tmp/x", "/tmp/r", password="p")
        assert not ok
        assert "not found" in detail
    finally:
        cloud_dog._restic_binary = old


def test_register_restic_records_snapshot():
    import shutil
    old_pw = _restic_test_env()
    reg_path = cloud_dog.REGISTRY_PATH
    backup = None
    if os.path.exists(reg_path):
        backup = reg_path + ".test-bak"
        os.replace(reg_path, backup)
    d = tempfile.mkdtemp(prefix="cdrestic-")
    old_audit = cloud_dog.AUDIT_PATH
    cloud_dog.AUDIT_PATH = os.path.join(d, "audit.jsonl")
    try:
        repo = os.path.join(d, "repo")
        f1 = os.path.join(d, "reg.txt")
        with open(f1, "w") as f:
            f.write("register-me")
        rc = cloud_dog.cmd_register(type("A", (), {
            "file": f1, "store": "restic", "repo": "", "release": "",
            "asset": "", "remote_dir": "", "dataset": "",
            "restic_repo": repo, "snapshot_id": "",
            "sha256": "", "size": 0, "note": "test",
        })())
        assert rc == 0
        reg = cloud_dog.load_registry()
        entry = reg["entries"].get(os.path.abspath(f1))
        assert entry is not None
        assert entry["store"] == "restic"
        assert entry["restic_repo"] == repo
        assert len(entry["snapshot_id"]) == 64
        # and the recorded snapshot fails closed on verify (metadata,
        # not a byte-level proof, per owner decision 2026-10-19)
        ok, detail = cloud_dog.restic_verify(entry, os.path.abspath(f1))
        assert not ok and "fail closed" in detail
    finally:
        cloud_dog.AUDIT_PATH = old_audit
        _restic_restore_env(old_pw)
        shutil.rmtree(d, ignore_errors=True)
        if os.path.exists(reg_path):
            os.unlink(reg_path)
        if backup and os.path.exists(backup):
            os.replace(backup, reg_path)
# ------------------------------------------------- mirror-search (IA) tests
def _fake_ia_urlopen_factory(payload_bytes=None, http_code=None):
    import urllib.request
    import urllib.error
    import json as _json

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return payload_bytes

    def fake_urlopen(req, timeout=60):
        if http_code is not None:
            raise urllib.error.HTTPError(req.full_url, http_code, "err",
                                         {}, None)
        return FakeResp()
    return fake_urlopen


def test_ia_download_url_quoting():
    u = cloud_dog.ia_download_url("some-id", "dir/my file (1).txt")
    assert u == ("https://archive.org/download/some-id/"
                 "dir/my%20file%20%281%29.txt"), u
    u2 = cloud_dog.ia_download_url("some-id", "plain.bin")
    assert u2 == "https://archive.org/download/some-id/plain.bin"


def test_ia_search_parses_docs():
    import urllib.request
    import json as _json
    payload = _json.dumps({
        "response": {"numFound": 2, "docs": [
            {"identifier": "abc123", "title": "Test Title"},
            {"identifier": "def456", "title": ["Multi", "Title"]},
        ]}}).encode()
    old = _patch(urllib.request, "urlopen",
                 _fake_ia_urlopen_factory(payload))
    try:
        ok, docs = cloud_dog.ia_search("whatever", rows=2)
    finally:
        _patch(urllib.request, "urlopen", old)
    assert ok is True
    assert [d["identifier"] for d in docs] == ["abc123", "def456"]


def test_ia_search_http_error():
    import urllib.request
    old = _patch(urllib.request, "urlopen",
                 _fake_ia_urlopen_factory(http_code=503))
    try:
        ok, detail = cloud_dog.ia_search("whatever")
    finally:
        _patch(urllib.request, "urlopen", old)
    assert ok is False
    assert "503" in detail


def test_ia_search_bad_json_shape():
    import urllib.request
    import json as _json
    payload = _json.dumps({"unexpected": True}).encode()
    old = _patch(urllib.request, "urlopen",
                 _fake_ia_urlopen_factory(payload))
    try:
        ok, detail = cloud_dog.ia_search("whatever")
    finally:
        _patch(urllib.request, "urlopen", old)
    assert ok is False
    assert "unexpected JSON" in detail


def test_ia_item_files_parses():
    import urllib.request
    import json as _json
    payload = _json.dumps({
        "metadata": {"identifier": "abc123", "title": "T"},
        "files": [{"name": "a.bin", "size": "100"},
                  {"name": "b.bin", "size": 200}]}).encode()
    old = _patch(urllib.request, "urlopen",
                 _fake_ia_urlopen_factory(payload))
    try:
        ok, files = cloud_dog.ia_item_files("abc123")
    finally:
        _patch(urllib.request, "urlopen", old)
    assert ok is True
    assert [f["name"] for f in files] == ["a.bin", "b.bin"]


def test_ia_item_files_no_file_list():
    import urllib.request
    import json as _json
    payload = _json.dumps({"metadata": {}}).encode()
    old = _patch(urllib.request, "urlopen",
                 _fake_ia_urlopen_factory(payload))
    try:
        ok, detail = cloud_dog.ia_item_files("abc123")
    finally:
        _patch(urllib.request, "urlopen", old)
    assert ok is False
    assert "no file list" in detail


def test_cmd_mirror_search_json_output():
    import io, contextlib
    import json as _json
    o1 = _patch(cloud_dog, "ia_search",
                lambda q, rows=10, timeout=60: (True, [
                    {"identifier": "abc123", "title": "Test Title"}]))
    o2 = _patch(cloud_dog, "ia_item_files",
                lambda ident, timeout=60: (True, [
                    {"name": "a.bin", "size": "100"}]))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cloud_dog.cmd_mirror_search(
                type("A", (), {"query": "x", "limit": 5, "json": True})())
        data = _json.loads(buf.getvalue())
    finally:
        _patch(cloud_dog, "ia_search", o1)
        _patch(cloud_dog, "ia_item_files", o2)
    assert rc == 0
    assert len(data) == 1
    assert data[0]["identifier"] == "abc123"
    assert data[0]["title"] == "Test Title"
    dl = data[0]["downloads"][0]
    assert dl["url"] == "https://archive.org/download/abc123/a.bin"
    assert dl["size"] == 100


def test_cmd_mirror_search_metadata_failure_keeps_going():
    import io, contextlib
    o1 = _patch(cloud_dog, "ia_search",
                lambda q, rows=10, timeout=60: (True, [
                    {"identifier": "bad1", "title": "Bad"},
                    {"identifier": "good2", "title": "Good"}]))
    def fake_files(ident, timeout=60):
        if ident == "bad1":
            return False, "IA metadata HTTP 404 for bad1"
        return True, [{"name": "g.bin", "size": 1}]
    o2 = _patch(cloud_dog, "ia_item_files", fake_files)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cloud_dog.cmd_mirror_search(
                type("A", (), {"query": "x", "limit": 5, "json": False})())
        out = buf.getvalue()
    finally:
        _patch(cloud_dog, "ia_search", o1)
        _patch(cloud_dog, "ia_item_files", o2)
    assert rc == 0
    assert "file list unavailable" in out
    assert "https://archive.org/download/good2/g.bin" in out


def test_cmd_mirror_search_search_failure():
    import io, contextlib
    o1 = _patch(cloud_dog, "ia_search",
                lambda q, rows=10, timeout=60: (False, "IA search HTTP 503"))
    try:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = cloud_dog.cmd_mirror_search(
                type("A", (), {"query": "x", "limit": 5, "json": False})())
    finally:
        _patch(cloud_dog, "ia_search", o1)
    assert rc == 1


def test_cmd_mirror_search_live():
    """LIVE test: real Internet Archive API, public-domain query."""
    import io, contextlib
    import json as _json
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cloud_dog.cmd_mirror_search(
            type("A", (), {"query": "shakespeare", "limit": 1,
                            "json": True})())
    assert rc == 0, "live IA search failed"
    data = _json.loads(buf.getvalue())
    assert len(data) >= 1
    assert data[0]["identifier"], "no identifier returned"
    assert len(data[0]["downloads"]) >= 1, "no download URLs returned"
    assert data[0]["downloads"][0]["url"].startswith(
        "https://archive.org/download/"), data[0]["downloads"][0]["url"]
# --------------------------------------------------- blake3 fast-hash tests
def test_fast_hash_file_uses_blake3_when_available():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"blake3-test-bytes")
        p = f.name
    try:
        if not cloud_dog._blake3_available():
            raise AssertionError("blake3 should be installed for this test")
        import blake3
        expected = blake3.blake3(b"blake3-test-bytes").hexdigest()
        assert cloud_dog.fast_hash_file(p) == expected
        assert len(expected) == 64
    finally:
        os.unlink(p)


def test_fast_hash_file_falls_back_to_sha256():
    import sys, tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"fallback-bytes")
        p = f.name
    old = sys.modules.get("blake3")
    sys.modules["blake3"] = None  # force ImportError on 'import blake3'
    old_b3 = _patch(cloud_dog, "_b3sum_bin", lambda: None)
    try:
        import hashlib
        expected = hashlib.sha256(b"fallback-bytes").hexdigest()
        assert cloud_dog.fast_hash_file(p) == expected
    finally:
        _patch(cloud_dog, "_b3sum_bin", old_b3)
        if old is None:
            del sys.modules["blake3"]
        else:
            sys.modules["blake3"] = old
        os.unlink(p)


def test_fast_hash_agrees_on_identical_files():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"same")
        p1 = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"same")
        p2 = f.name
    try:
        assert cloud_dog.fast_hash_file(p1) == cloud_dog.fast_hash_file(p2)
    finally:
        os.unlink(p1)
        os.unlink(p2)


# ----------------- hash cmd + agent-audit + dust Mi fix (2026-10-17)
import hashlib as _hashlib
import inspect as _inspect
import pytest as _pytest


def _write_tmp_file(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_cmd_hash_json(tmp_path, capsys):
    f = _write_tmp_file(tmp_path, "h.bin", b"hashme")
    rc = cloud_dog.cmd_hash(type("A", (), {"file": f, "json": True})())
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["digest"] == cloud_dog.fast_hash_file(f)
    assert out["bytes"] == 6
    assert out["backend"].startswith(("blake3", "sha256"))
    assert out["mb_per_s"] >= 0


def test_cmd_hash_text_names_backend(tmp_path, capsys):
    f = _write_tmp_file(tmp_path, "h2.bin", b"hashme2")
    rc = cloud_dog.cmd_hash(type("A", (), {"file": f, "json": False})())
    assert rc == 0
    out = capsys.readouterr().out
    assert cloud_dog.fast_hash_file(f) in out


def test_cmd_hash_missing_file(capsys):
    rc = cloud_dog.cmd_hash(type("A", (), {
        "file": "/no/such/file.bin", "json": False})())
    assert rc == 1


def test_stream_url_to_parts_accepts_hasher_factory():
    sig = _inspect.signature(cloud_dog.stream_url_to_parts)
    assert "hasher_factory" in sig.parameters


def test_parse_size():
    assert cloud_dog._parse_size("20G") == 20 * (1 << 30)
    assert cloud_dog._parse_size("100M") == 100 * (1 << 20)
    assert cloud_dog._parse_size("512") == 512
    assert cloud_dog._parse_size("1.5G") == int(1.5 * (1 << 30))
    with _pytest.raises(ValueError):
        cloud_dog._parse_size("ten-gig")


def test_parse_dust_size_iec_suffix():
    # dust 1.2.6 emits IEC 'i' suffixes; same multipliers as K/M/G.
    # Without this, the bundled dust's sizes all parse to 0 and the
    # scan/audit fast paths go blind.
    assert cloud_dog._parse_dust_size("1.0Mi") == 1024 ** 2
    assert cloud_dog._parse_dust_size("512Ki") == 512 * 1024
    assert cloud_dog._parse_dust_size("2.0Gi") == 2 * 1024 ** 3
    assert cloud_dog._parse_dust_size("1.0Ti") == 1024 ** 4
    # old format still works
    assert cloud_dog._parse_dust_size("2.1M") == int(2.1 * 1024 ** 2)


def _make_agent_tree(tmp_path):
    # root/
    #   lane-alpha/ big.bin (1MB), small.bin (100KB)
    #   lane-beta/  notes.txt (50B)
    root = tmp_path / "ws"
    root.mkdir()
    a = root / "lane-alpha"
    a.mkdir()
    (a / "big.bin").write_bytes(b"A" * (1 << 20))
    (a / "small.bin").write_bytes(b"B" * (100 << 10))
    b = root / "lane-beta"
    b.mkdir()
    (b / "notes.txt").write_bytes(b"C" * 50)
    return str(root), str(a), str(b)


def test_agent_audit_report_coverage(monkeypatch, tmp_path):
    # force the stdlib walk path (exact bytes) — dust -j reports rounded
    # human sizes, so exact assertions only hold on the walk path
    monkeypatch.setattr(cloud_dog, "_dust_binary", lambda: None)
    root, a, b = _make_agent_tree(tmp_path)
    reg = {"version": 1, "entries": {
        os.path.join(a, "big.bin"): {"store": "github", "size": 1 << 20,
                                     "status": "registered"},
        os.path.join(a, "gone.bin"): {"store": "github", "size": 500,
                                      "status": "reaped"},
        os.path.join(b, "old.bin"): {"store": "terabox", "size": 25,
                                     "status": "registered"},
    }}
    rows = cloud_dog.agent_audit_report(
        root=root, registry=reg, top=0, min_uncovered_bytes=10)
    by_name = {r["name"]: r for r in rows}
    alpha = by_name["lane-alpha"]
    assert alpha["bytes"] == (1 << 20) + (100 << 10)
    assert alpha["covered_bytes"] == (1 << 20)
    assert alpha["cleared_bytes"] == 500
    assert alpha["unregistered_bytes"] == (100 << 10)
    assert 90.0 < alpha["coverage_pct"] < 100.0
    assert alpha["flags"] == []
    assert alpha["files"] == 2
    # biggest dir sorts first
    assert rows[0]["name"] == "lane-alpha"


def test_agent_audit_report_dust_path_approximate(tmp_path):
    # dust fast path: sizes are human-rounded, so assert approximate
    if cloud_dog._dust_binary() is None:
        _pytest.skip("no dust binary available")
    root, a, b = _make_agent_tree(tmp_path)
    reg = {"version": 1, "entries": {}}
    rows = cloud_dog.agent_audit_report(
        root=root, registry=reg, top=0, min_uncovered_bytes=10)
    by_name = {r["name"]: r for r in rows}
    alpha = by_name["lane-alpha"]
    assert abs(alpha["bytes"] - ((1 << 20) + (100 << 10))) < 0.05 * (1 << 20)
    assert alpha["files"] is None  # dust reports bytes only
    assert rows[0]["name"] == "lane-alpha"


def test_agent_audit_report_flags(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_dog, "_dust_binary", lambda: None)
    root, a, b = _make_agent_tree(tmp_path)
    reg = {"version": 1, "entries": {}}
    rows = cloud_dog.agent_audit_report(
        root=root, registry=reg, top=0, quota_bytes=1000,
        min_uncovered_bytes=10)
    by_name = {r["name"]: r for r in rows}
    alpha = by_name["lane-alpha"]
    assert "over_quota" in alpha["flags"]
    assert "uncovered_big" in alpha["flags"]
    assert alpha["coverage_pct"] == 0.0
    beta = by_name["lane-beta"]
    assert "over_quota" not in beta["flags"]
    assert "uncovered_big" in beta["flags"]


def test_cmd_agent_audit_json(tmp_path, capsys):
    root, a, b = _make_agent_tree(tmp_path)
    db_path = cloud_dog.REGISTRY_DB_PATH
    reg_path = cloud_dog.REGISTRY_PATH
    backups = []
    for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.exists(p):
            bak = p + ".test-bak"
            os.replace(p, bak)
            backups.append((p, bak))
    import tempfile
    audit_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
    audit_tmp.close()
    old_audit = _patch(cloud_dog, "AUDIT_PATH", audit_tmp.name)
    try:
        rc = cloud_dog.cmd_agent_audit(type("A", (), {
            "quota": "1G", "top": 0, "root": root,
            "min_uncovered": "1", "json": True})())
        assert rc == 0
        out = _json.loads(capsys.readouterr().out)
        assert out["quota_bytes"] == 1 << 30
        names = [r["name"] for r in out["rows"]]
        assert "lane-alpha" in names and "lane-beta" in names
    finally:
        _patch(cloud_dog, "AUDIT_PATH", old_audit)
        if os.path.exists(audit_tmp.name):
            os.unlink(audit_tmp.name)
        for p in (reg_path, db_path, db_path + "-wal", db_path + "-shm"):
            if os.path.exists(p):
                os.unlink(p)
        for p, bak in backups:
            if os.path.exists(bak):
                os.replace(bak, p)


# --------------------------------------------------- xxhash3 tests
def test_xxh3_file_matches_and_differs():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"xxh3-test-bytes")
        p1 = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"xxh3-test-bytes")
        p2 = f.name
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"different-bytes-here")
        p3 = f.name
    try:
        h1 = cloud_dog.xxh3_file(p1)
        h2 = cloud_dog.xxh3_file(p2)
        h3 = cloud_dog.xxh3_file(p3)
        if h1 is None:
            import pytest
            pytest.skip("xxhash not installed")
        assert h1 == h2, "identical files must match"
        assert h1 != h3, "different files must differ"
        assert len(h1) == 16, "xxh3_64 hex is 16 chars"
    finally:
        for p in (p1, p2, p3):
            os.unlink(p)


def test_xxh3_available():
    # Should be True on this VM (pip installed); just check it doesn't crash
    assert isinstance(cloud_dog._xxhash_available(), bool)


# --------------------------------------------------- zfec shard tests
def test_shard_unshard_roundtrip():
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="cdshard-", dir=os.path.expanduser("~/workspace"))
    try:
        src = os.path.join(d, "data.bin")
        with open(src, "wb") as f:
            f.write(b"shard-test-payload-" * 100)
        sdir = os.path.join(d, "shards")
        rc = cloud_dog.cmd_shard(type("A", (), {
            "file": src, "k": 2, "m": 4, "out": sdir})())
        assert rc == 0
        # 4 shards + meta
        shards = [f for f in os.listdir(sdir)
                  if ".shard-" in f and not f.endswith(".json")]
        assert len(shards) == 4, shards
        # Delete 2 shards (any 2 of 4 reconstruct)
        os.unlink(os.path.join(sdir, "data.bin.shard-2-of-4"))
        os.unlink(os.path.join(sdir, "data.bin.shard-3-of-4"))
        out = os.path.join(d, "restored.bin")
        rc = cloud_dog.cmd_unshard(type("A", (), {
            "dir": sdir, "out": out})())
        assert rc == 0
        with open(src, "rb") as f:
            orig = f.read()
        with open(out, "rb") as f:
            rest = f.read()
        assert orig == rest, "reconstructed bytes must match original"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_shard_needs_zfec():
    old = _patch(cloud_dog, "_zfec_available", lambda: False)
    try:
        rc = cloud_dog.cmd_shard(type("A", (), {
            "file": "/tmp/x", "k": 2, "m": 4, "out": ""})())
        assert rc == 1
    finally:
        _patch(cloud_dog, "_zfec_available", old)


# --------------------------------------------------- b2 store tests
def test_rclone_binary_found():
    result = cloud_dog._rclone_binary()
    # Should find ~/workspace/tools/rclone/rclone on this VM
    assert result is not None, "rclone binary should be found"
    assert os.path.isfile(result)


def test_b2_upload_needs_credentials():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        f.write(b"b2-test")
        p = f.name
    # Ensure no creds in env
    old_id = os.environ.pop("B2_ACCOUNT_ID", None)
    old_key = os.environ.pop("B2_ACCOUNT_KEY", None)
    try:
        ok, err = cloud_dog.b2_upload(p, "test-bucket", "test/path.dat")
        assert not ok
        assert "B2_ACCOUNT_ID" in err
    finally:
        if old_id:
            os.environ["B2_ACCOUNT_ID"] = old_id
        if old_key:
            os.environ["B2_ACCOUNT_KEY"] = old_key
        os.unlink(p)


def test_b2_upload_needs_binary():
    import tempfile
    old = _patch(cloud_dog, "_rclone_binary", lambda: None)
    try:
        with tempfile.NamedTemporaryFile(delete=False) as f:
            p = f.name
        try:
            ok, err = cloud_dog.b2_upload(p, "b", "p")
            assert not ok
            assert "rclone" in err.lower()
        finally:
            os.unlink(p)
    finally:
        _patch(cloud_dog, "_rclone_binary", old)


def test_b2_verify_needs_credentials():
    old_id = os.environ.pop("B2_ACCOUNT_ID", None)
    old_key = os.environ.pop("B2_ACCOUNT_KEY", None)
    try:
        entry = {"store": "b2", "b2_bucket": "b", "b2_path": "p", "size": 100}
        ok, err = cloud_dog.b2_verify(entry)
        assert not ok
        assert "fail closed" in err
    finally:
        if old_id:
            os.environ["B2_ACCOUNT_ID"] = old_id
        if old_key:
            os.environ["B2_ACCOUNT_KEY"] = old_key


def test_b2_verify_needs_bucket_and_path():
    # With fake creds, should fail on missing bucket/path before network
    os.environ["B2_ACCOUNT_ID"] = "fake-id"
    os.environ["B2_ACCOUNT_KEY"] = "fake-key"
    try:
        entry = {"store": "b2", "size": 100}  # no bucket/path
        ok, err = cloud_dog.b2_verify(entry)
        assert not ok
        assert "fail closed" in err
    finally:
        del os.environ["B2_ACCOUNT_ID"]
        del os.environ["B2_ACCOUNT_KEY"]


def test_verify_entry_dispatches_b2():
    # verify_entry should route b2 store to b2_verify (fails on creds, not unknown store)
    old_id = os.environ.pop("B2_ACCOUNT_ID", None)
    old_key = os.environ.pop("B2_ACCOUNT_KEY", None)
    try:
        entry = {"store": "b2", "b2_bucket": "b", "b2_path": "p", "size": 1}
        ok, err = cloud_dog.verify_entry(entry, "/tmp/x")
        assert not ok
        assert "unknown store" not in err, f"should dispatch to b2, got: {err}"
    finally:
        if old_id:
            os.environ["B2_ACCOUNT_ID"] = old_id
        if old_key:
            os.environ["B2_ACCOUNT_KEY"] = old_key


# ------------------------------------------------ owner decisions 2026-10-19

def test_part_bytes_is_the_100mb_standard():
    assert cloud_dog.PART_BYTES == 100 * 1024 * 1024


class _FakeVerifyProof:
    def __init__(self, proven, reason="", bytes_fetched=0):
        self.proven = proven
        self.reason = reason
        self.bytes_fetched = bytes_fetched


def test_verify_github_asset_full_redownload_proven():
    # verify_github_asset must go through the pull-back prover and pass
    # ONLY when the re-downloaded bytes hash-match.
    old = _patch(cloud_dog, "_run_retrievability_proof",
                 lambda entry: _FakeVerifyProof(True, "ok", bytes_fetched=123))
    try:
        ok, detail = cloud_dog.verify_github_asset(
            "o/r", "tag", "a.bin", 123, "ab" * 32)
        assert ok is True and "full re-download" in detail
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)


def test_verify_github_asset_hash_mismatch_fails():
    old = _patch(cloud_dog, "_run_retrievability_proof",
                 lambda entry: _FakeVerifyProof(
                     False, "sha256 mismatch after pull-back"))
    try:
        ok, detail = cloud_dog.verify_github_asset(
            "o/r", "tag", "a.bin", 123, "ab" * 32)
        assert ok is False and "sha256 mismatch" in detail
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)


def test_verify_github_asset_prover_unavailable_fails_closed():
    old = _patch(cloud_dog, "_run_retrievability_proof", lambda entry: None)
    try:
        ok, detail = cloud_dog.verify_github_asset(
            "o/r", "tag", "a.bin", 123, "ab" * 32)
        assert ok is False and "fail closed" in detail
    finally:
        _patch(cloud_dog, "_run_retrievability_proof", old)


def _claims_test_env(tmp):
    """Point cloud_dog's registry/claims/log at a tmp dir; return restore fn."""
    olds = {
        "REGISTRY_PATH": _patch(cloud_dog, "REGISTRY_PATH",
                                os.path.join(tmp, "reg.json")),
        "REGISTRY_DB_PATH": _patch(cloud_dog, "REGISTRY_DB_PATH",
                                   os.path.join(tmp, "reg.db")),
        "LOG_PATH": _patch(cloud_dog, "LOG_PATH",
                           os.path.join(tmp, "cleanup.log")),
    }

    def restore():
        for name, old in olds.items():
            _patch(cloud_dog, name, old)

    return restore


def test_reap_candidates_claim_gate_blocks_and_release_frees(tmp_path=None):
    # The Tekken 5 fix at the engine level: an OLD file (past the 5-min
    # in-flight mtime heuristic) with a verified registry entry is still
    # NOT a reap candidate while a lane's claim is live.
    import cloud_dog_registry as regmod
    tmp = tempfile.mkdtemp()
    restore = _claims_test_env(tmp)
    try:
        target = os.path.join(tmp, "tekken5.iso")
        with open(target, "wb") as f:
            f.write(b"x" * 1024)
        old = os.path.getmtime(target) - 3600  # 1h old: heuristic says free
        os.utime(target, (old, old))
        cloud_dog.save_registry({"version": 4, "entries": {target: {
            "store": "github", "repo": "o/r", "release": "t", "asset": "a",
            "size": 1024, "sha256": "s", "status": "registered"}}})
        # unclaimed: it IS a candidate (the old world — heuristic only)
        assert [p for p, _ in cloud_dog.reap_candidates()] == [target]
        # claimed: hard-blocked despite age + verified entry
        ok, claim = regmod.claim_path(cloud_dog.REGISTRY_DB_PATH, target,
                                      "t5-iso-dl", "download",
                                      json_path=cloud_dog.REGISTRY_PATH)
        assert ok
        assert list(cloud_dog.reap_candidates()) == []
        # released: candidate again
        assert regmod.release_claim(cloud_dog.REGISTRY_DB_PATH, target,
                                    claim["claim_id"],
                                    json_path=cloud_dog.REGISTRY_PATH)
        assert [p for p, _ in cloud_dog.reap_candidates()] == [target]
    finally:
        restore()


def test_reap_candidates_fail_closed_on_unreadable_claims(tmp_path=None):
    # Corrupt claims DB => reap_candidates yields NOTHING (fail closed).
    tmp = tempfile.mkdtemp()
    restore = _claims_test_env(tmp)
    try:
        target = os.path.join(tmp, "file.bin")
        with open(target, "wb") as f:
            f.write(b"x" * 64)
        old = os.path.getmtime(target) - 3600
        os.utime(target, (old, old))
        cloud_dog.save_registry({"version": 4, "entries": {target: {
            "store": "github", "repo": "o/r", "release": "t", "asset": "a",
            "size": 64, "sha256": "s", "status": "registered"}}})
        with open(cloud_dog.REGISTRY_DB_PATH, "wb") as f:
            f.write(b"not a sqlite db" * 128)
        assert list(cloud_dog.reap_candidates()) == []
        assert os.path.exists(target)  # and the file is untouched
    finally:
        restore()


def test_claims_cli_check_exit_codes(tmp_path=None):
    # `claims check` contract for non-CloudDog sweep lanes:
    # exit 0 = claimed/BLOCKED (do not delete), exit 1 = free.
    import cloud_dog_registry as regmod
    tmp = tempfile.mkdtemp()
    restore = _claims_test_env(tmp)
    try:
        target = os.path.join(tmp, "work.bin")
        with open(target, "wb") as f:
            f.write(b"x")
        args = type("A", (), {"claims_cmd": "check", "path": target})()
        assert cloud_dog.cmd_claims(args) == 1  # free
        ok, claim = regmod.claim_path(cloud_dog.REGISTRY_DB_PATH, target,
                                      "lane", "download",
                                      json_path=cloud_dog.REGISTRY_PATH)
        assert ok
        assert cloud_dog.cmd_claims(args) == 0  # claimed: do not delete
    finally:
        restore()


# --------------------------------------- claims schema migration (2026-10-20)
def _write_pre_claims_db(db_path):
    """A registry DB in the pre-#43 shape: v4 entries/journal/meta, but
    NO claims table (the live state that made every reap fail closed
    with 'claims unreadable (no such table: claims)')."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE entries (local_path TEXT PRIMARY KEY,"
            " store TEXT NOT NULL DEFAULT '', repo TEXT NOT NULL DEFAULT '',"
            " release TEXT NOT NULL DEFAULT '', asset TEXT NOT NULL DEFAULT '',"
            " size INTEGER NOT NULL DEFAULT 0, sha256 TEXT NOT NULL DEFAULT '',"
            " status TEXT NOT NULL DEFAULT 'registered',"
            " registered_at TEXT NOT NULL DEFAULT '',"
            " reaped_at TEXT NOT NULL DEFAULT '',"
            " verified_at TEXT NOT NULL DEFAULT '',"
            " updated_at TEXT NOT NULL DEFAULT '',"
            " extra TEXT NOT NULL DEFAULT '{}')")
        conn.execute(
            "CREATE TABLE journal (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts TEXT NOT NULL, action TEXT NOT NULL,"
            " local_path TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '')")
        conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        conn.commit()
    finally:
        conn.close()


def _claims_tables(db_path):
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_claims_readable_migrates_pre_claims_db(tmp_path=None):
    """A pre-claims DB gains the claims table via the probe's migration
    path (ensure_claims_schema) instead of failing closed forever."""
    import cloud_dog_registry as regmod
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "reg.db")
    _write_pre_claims_db(db)
    assert "claims" not in _claims_tables(db)
    readable, reason = regmod.claims_readable(db, json_path=None)
    assert readable, reason
    assert "claims" in _claims_tables(db)
    ok, detail = regmod.ensure_claims_schema(db, json_path=None)
    assert ok, detail
    # And the migrated store actually gates: unclaimed = allowed,
    # claimed = blocked.
    allowed, _ = regmod.claim_gate(db, os.path.join(tmp, "f.bin"), json_path=None)
    assert allowed
    ok, claim = regmod.claim_path(db, os.path.join(tmp, "f.bin"), "lane",
                                  "download", json_path=None)
    assert ok
    allowed, reason = regmod.claim_gate(db, os.path.join(tmp, "f.bin"),
                                        json_path=None)
    assert not allowed and "lane" in reason


def test_claims_readable_heals_db_swapped_under_long_lived_process(tmp_path=None):
    """The stale-init-cache case: a process initialized the current DB,
    then the file is replaced by a pre-claims DB (restore/swap). The
    probe must not trust its cached init — it migrates and reads."""
    import cloud_dog_registry as regmod
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "reg.db")
    ok, detail = regmod.ensure_claims_schema(db, json_path=None)
    assert ok, detail  # first open: schema init cached for this path
    # Swap the file for a pre-claims DB underneath the process.
    for suffix in ("-wal", "-shm"):
        side = db + suffix
        if os.path.exists(side):
            os.unlink(side)
    os.unlink(db)
    _write_pre_claims_db(db)
    assert "claims" not in _claims_tables(db)
    readable, reason = regmod.claims_readable(db, json_path=None)
    assert readable, reason
    assert "claims" in _claims_tables(db)


def test_claims_migrate_cli(tmp_path=None):
    tmp = tempfile.mkdtemp()
    restore = _claims_test_env(tmp)
    try:
        _write_pre_claims_db(cloud_dog.REGISTRY_DB_PATH)
        assert "claims" not in _claims_tables(cloud_dog.REGISTRY_DB_PATH)
        args = type("A", (), {"claims_cmd": "migrate"})()
        assert cloud_dog.cmd_claims(args) == 0
        assert "claims" in _claims_tables(cloud_dog.REGISTRY_DB_PATH)
    finally:
        restore()


def test_cmd_reap_policy_leg_blocks_keep_local(tmp_path=None):
    """Delete-gate policy leg for cmd_reap (2026-10-20 incident fix):
    a registered, live-verifiable file that evaluate() calls
    KEEP_LOCAL must survive cmd_reap --apply; a cold STORE_AND_CLEAR
    file with the same stubbed verify is reaped (positive control)."""
    tmp = tempfile.mkdtemp()
    restore = _claims_test_env(tmp)
    old_audit = _patch(cloud_dog, "AUDIT_PATH", os.path.join(tmp, "audit.jsonl"))
    old_verify = _patch(cloud_dog, "verify_github_asset",
                        lambda *a, **k: (True, "stubbed ok"))
    try:
        # KEEP_LOCAL: small file, 10 min old (past the in-flight window,
        # still inside evaluate()'s 1h hot window).
        keep = os.path.join(tmp, "keep.bin")
        with open(keep, "wb") as f:
            f.write(b"k" * 100)
        old = os.path.getmtime(keep) - 600
        os.utime(keep, (old, old))
        # STORE_AND_CLEAR control: 12MB sparse, 2h old.
        cold = os.path.join(tmp, "cold.bin")
        with open(cold, "wb") as f:
            f.truncate(12 * 1024 * 1024)
        old = os.path.getmtime(cold) - 7200
        os.utime(cold, (old, old))
        cloud_dog.save_registry({"version": 4, "entries": {
            keep: {"store": "github", "repo": "o/r", "release": "t",
                   "asset": "keep.bin", "size": 100, "sha256": "s",
                   "status": "registered"},
            cold: {"store": "github", "repo": "o/r", "release": "t",
                   "asset": "cold.bin", "size": 12 * 1024 * 1024,
                   "sha256": "s", "status": "registered"},
        }})
        args = type("A", (), {"apply": True, "path": ""})()
        assert cloud_dog.cmd_reap(args) == 0
        assert os.path.exists(keep), "KEEP_LOCAL file must survive cmd_reap"
        assert not os.path.exists(cold), "STORE_AND_CLEAR control must reap"
    finally:
        _patch(cloud_dog, "verify_github_asset", old_verify)
        _patch(cloud_dog, "AUDIT_PATH", old_audit)
        restore()


# ======================================================= training layer
# Scorecard adapter (2026-10-20): guardian/root audit actions are graded
# as a first-class source; correctness first, freed bytes last. State
# (trust) resolves through the state dir, never a repo checkout, and
# these tests only ever point it at tmp dirs.

def test_scorecard_adapter_grades_root_actions():
    rec = cloud_dog.scorecard_record_from_audit(
        {"action": "reap", "path": "/w/a.bin", "size": 2048})
    assert rec["grade"] == "CORRECT" and rec["freed"] == 2048
    assert "clear" in rec["note"]
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "reap_policy_block", "path": "/w/b.bin"})["grade"] == "BLOCKED"
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "reap_keep", "path": "/w/c.bin"})["grade"] == "BLOCKED"
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "verify", "ok": True})["grade"] == "CORRECT"
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "verify", "ok": False})["grade"] == "BLOCKED"
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "daemon_cycle", "disk_pct": 90.0}) is None
    assert cloud_dog.scorecard_record_from_audit(
        {"action": "reap_summary", "count": 3}) is None


def test_scorecard_counts_merge_root_and_v1(tmp_path=None):
    import json as _json
    tmp = str(tmp_path) if tmp_path else tempfile.mkdtemp()
    root_j = os.path.join(tmp, "root.jsonl")
    with open(root_j, "w") as f:
        f.write(_json.dumps({"action": "reap", "path": "/a", "size": 100}) + "\n")
        f.write(_json.dumps({"action": "reap_policy_block", "path": "/b"}) + "\n")
    v1_j = os.path.join(tmp, "v1.jsonl")
    with open(v1_j, "w") as f:
        f.write(_json.dumps({"kind": "scorecard", "grade": "NEAR_MISS",
                             "path": "/c", "note": "one leg",
                             "regression_test": None}) + "\n")
        f.write(_json.dumps({"kind": "cleared", "path": "/d", "size": 50}) + "\n")
    old_env = os.environ.get("CLOUDEDOG_STATE_DIR")
    os.environ["CLOUDEDOG_STATE_DIR"] = tmp  # no trust.json in tmp -> REVOKED
    try:
        counts = cloud_dog.scorecard_counts(root_journal=root_j, v1_journal=v1_j)
    finally:
        if old_env is None:
            os.environ.pop("CLOUDEDOG_STATE_DIR", None)
        else:
            os.environ["CLOUDEDOG_STATE_DIR"] = old_env
    assert counts["correct_clears"] == 1
    assert counts["correct_blocks"] == 1
    assert counts["near_misses"] == 1
    assert counts["freed_bytes"] == 150  # root reap 100 + v1 cleared 50
    assert counts["trust_state"] == "REVOKED"
    assert len(counts["missing_test_incidents"]) == 1
    line = cloud_dog.scorecard_line(counts)
    assert line.index("correct_clears") < line.index("freed_bytes")
