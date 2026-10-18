"""Tests for vvram_allocator.py — virtual memory & compute pool.

Covers the compute probe (honest GPU reporting), mmap hashing
(correctness vs plain reads), parallel hashing, and the buffer pool.
No GPU is required; the probe must report its absence truthfully.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import vvram_allocator as V


def test_probe_compute_honest():
    info = V.probe_compute()
    assert info["cpu_count"] >= 1
    assert info["mmap_available"] is True
    # This host has no GPU — the probe must say so, not fake one.
    assert info["gpu"] is None
    assert info["cuda"] is False
    assert info["rocm"] is False


def test_mmap_hash_matches_plain_read(tmp_path):
    p = tmp_path / "data.bin"
    data = os.urandom(3_000_000)
    p.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert V.mmap_hash_file(str(p), "sha256") == expected


def test_mmap_hash_empty_file(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    # mmap cannot map 0 bytes; must fall back cleanly.
    assert V.mmap_hash_file(str(p), "sha256") == hashlib.sha256(b"").hexdigest()


def test_mmap_hash_blake3(tmp_path):
    blake3 = pytest.importorskip("blake3")
    p = tmp_path / "data.bin"
    data = os.urandom(1_000_000)
    p.write_bytes(data)
    assert V.mmap_hash_file(str(p), "blake3") == blake3.blake3(data).hexdigest()


def test_mmap_hash_large_uses_mmap_path(tmp_path, monkeypatch):
    # Force the mmap path with a small threshold and verify correctness.
    monkeypatch.setattr(V, "_MMAP_THRESHOLD", 1024)
    p = tmp_path / "big.bin"
    data = os.urandom(100_000)
    p.write_bytes(data)
    assert V.mmap_hash_file(str(p), "sha256") == hashlib.sha256(data).hexdigest()


def test_parallel_hash(tmp_path):
    files = []
    expected = {}
    for i in range(4):
        p = tmp_path / f"f{i}.bin"
        data = os.urandom(500_000 + i)
        p.write_bytes(data)
        files.append(str(p))
        expected[str(p)] = hashlib.sha256(data).hexdigest()
    assert V.parallel_hash(files, "sha256", workers=2) == expected


def test_parallel_hash_empty():
    assert V.parallel_hash([]) == {}


def test_parallel_hash_missing_file(tmp_path):
    # A missing file maps to None, never raises.
    res = V.parallel_hash([str(tmp_path / "nope.bin")], "sha256")
    assert res[str(tmp_path / "nope.bin")] is None


def test_allocator_reuse():
    a = V.VVRAMAllocator(chunk_size=1024, pool_size=2)
    b1 = a.acquire()
    assert len(b1) >= 1024
    a.release(b1)
    b2 = a.acquire()
    assert a.stats()["reused_total"] >= 1
    a.release(b2)
    assert a.stats()["cached"] == 1


def test_allocator_respects_pool_size():
    a = V.VVRAMAllocator(chunk_size=64, pool_size=1)
    bufs = [a.acquire() for _ in range(3)]
    for b in bufs:
        a.release(b)
    # Pool holds at most pool_size buffers; extras are dropped.
    assert a.stats()["cached"] <= 1


def test_import_has_no_side_effects():
    import subprocess
    out = subprocess.run(
        [sys.executable, "-c", "import vvram_allocator"],
        capture_output=True, text=True, check=False,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.returncode == 0
    assert out.stdout == "", f"import printed: {out.stdout!r}"
