"""Tests for vvram_allocator.py — virtual memory & compute pool.

Covers the compute probe (honest GPU reporting), mmap hashing
(correctness vs plain reads), parallel hashing, and the buffer pool.
No GPU is required; the probe must report its absence truthfully.

Note: temp dirs live under ~/workspace, not /tmp (512M tmpfs).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vvram_allocator as V


@pytest.fixture
def ws_tmp():
    d = tempfile.mkdtemp(prefix="vvram-test-", dir=os.path.expanduser("~/workspace"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


def test_probe_compute_honest():
    info = V.probe_compute()
    assert info["cpu_count"] >= 1
    assert info["mmap_available"] is True
    # This host has no GPU — the probe must say so, not fake one.
    assert info["gpu"] is None
    assert info["cuda"] is False
    assert info["rocm"] is False


def test_mmap_hash_matches_plain_read(ws_tmp):
    p = os.path.join(ws_tmp, "data.bin")
    data = os.urandom(3_000_000)
    _write(p, data)
    assert V.mmap_hash_file(p, "sha256") == hashlib.sha256(data).hexdigest()


def test_mmap_hash_empty_file(ws_tmp):
    p = os.path.join(ws_tmp, "empty.bin")
    _write(p, b"")
    # mmap cannot map 0 bytes; must fall back cleanly.
    assert V.mmap_hash_file(p, "sha256") == hashlib.sha256(b"").hexdigest()


def test_mmap_hash_blake3(ws_tmp):
    blake3 = pytest.importorskip("blake3")
    p = os.path.join(ws_tmp, "data.bin")
    data = os.urandom(1_000_000)
    _write(p, data)
    assert V.mmap_hash_file(p, "blake3") == blake3.blake3(data).hexdigest()


def test_mmap_hash_large_uses_mmap_path(ws_tmp, monkeypatch):
    # Force the mmap path with a small threshold and verify correctness.
    monkeypatch.setattr(V, "_MMAP_THRESHOLD", 1024)
    p = os.path.join(ws_tmp, "big.bin")
    data = os.urandom(100_000)
    _write(p, data)
    assert V.mmap_hash_file(p, "sha256") == hashlib.sha256(data).hexdigest()


def test_parallel_hash(ws_tmp):
    files = []
    expected = {}
    for i in range(4):
        p = os.path.join(ws_tmp, f"f{i}.bin")
        data = os.urandom(500_000 + i)
        _write(p, data)
        files.append(p)
        expected[p] = hashlib.sha256(data).hexdigest()
    assert V.parallel_hash(files, "sha256", workers=2) == expected


def test_parallel_hash_empty():
    assert V.parallel_hash([]) == {}


def test_parallel_hash_missing_file(ws_tmp):
    # A missing file maps to None, never raises.
    missing = os.path.join(ws_tmp, "nope.bin")
    assert V.parallel_hash([missing], "sha256") == {missing: None}


def test_allocator_reuse():
    a = V.VVRAMAllocator(chunk_size=1024, pool_size=2)
    b1 = a.acquire()
    assert len(b1) >= 1024
    a.release(b1)
    a.acquire()
    assert a.stats()["reused_total"] >= 1


def test_allocator_respects_pool_size():
    a = V.VVRAMAllocator(chunk_size=64, pool_size=1)
    bufs = [a.acquire() for _ in range(3)]
    for b in bufs:
        a.release(b)
    # Pool holds at most pool_size buffers; extras are dropped.
    assert a.stats()["cached"] <= 1


def test_import_has_no_side_effects():
    out = subprocess.run(
        [sys.executable, "-c", "import vvram_allocator"],
        capture_output=True,
        text=True,
        check=False,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert out.returncode == 0
    assert out.stdout == "", f"import printed: {out.stdout!r}"
