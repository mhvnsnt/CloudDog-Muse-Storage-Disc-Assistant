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
    # register + load must preserve the entry (uses real registry path;
    # cleans up after itself)
    import json
    reg_path = cloud_dog.REGISTRY_PATH
    backup = None
    if os.path.exists(reg_path):
        backup = reg_path + ".test-bak"
        os.replace(reg_path, backup)
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
        if os.path.exists(reg_path):
            os.unlink(reg_path)
        if backup and os.path.exists(backup):
            os.replace(backup, reg_path)


if __name__ == "__main__":
    # smoke subset without pytest
    test_sacred_paths()
    test_inflight_suffix()
    test_inflight_fresh_file()
    test_human()
    test_sha256_file()
    print("smoke tests passed")
