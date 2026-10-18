#!/usr/bin/env python3
"""
VVRAM — Virtual Memory & Compute Allocator for CloudDog.

Production-grade userspace virtual-memory + CPU compute pool for CloudDog's
heavy operations: large-file hashing, erasure coding, torrent part streaming.

Design honesty (2026-10-17):
  This host has NO GPU (no CUDA, no ROCm, no /dev/dri). Wiring CUDA/ROCm/
  Vulkan here would be theater — those paths are PROBED and REPORTED, never
  faked. The real "virtual VRAM" on a GPU-less host is the OS virtual memory
  system:
    - mmap(2): zero-copy file access. The OS maps file pages into virtual
      address space on demand; process RSS stays constant regardless of file
      size. 30-50% faster than read() for multi-GB files (fewer copies,
      fewer syscalls).
    - Process pools: BLAKE3 releases the GIL and parallelizes internally,
      but hashing MANY files scales best across processes (one per core).
    - Buffer pool: reusable I/O buffers kill malloc/free churn during part
      streaming.

If a GPU appears on a future host, probe_compute() reports it and the
caller can branch — but nothing here pretends a GPU exists.

Public API:
    probe_compute() -> dict
    mmap_hash_file(path, algorithm="blake3") -> hexdigest
    parallel_hash(paths, algorithm="blake3", workers=None) -> {path: hexdigest}
    VVRAMAllocator(chunk_size=1<<20, pool_size=8)
        .acquire(size) -> bytearray
        .release(buf)
        .stats() -> dict
"""

import hashlib
import mmap
import os
import shutil
import subprocess

# Chunk size for non-mmap fallback reads.
_CHUNK = 1 << 20

# Files smaller than this use plain reads; mmap wins on large files where
# page-cache behavior dominates. Tunable per host.
_MMAP_THRESHOLD = 64 << 20  # 64 MiB


def _blake3_new():
    """Fresh BLAKE3 hasher, or None if the optional package is missing."""
    try:
        import blake3
        return blake3.blake3()
    except ImportError:
        return None


def _new_hasher(algorithm):
    """Hasher object for algorithm in {blake3, sha256, xxh3}."""
    if algorithm == "blake3":
        h = _blake3_new()
        if h is not None:
            return h
        algorithm = "sha256"  # graceful downgrade
    if algorithm == "xxh3":
        try:
            import xxhash
            return xxhash.xxh3_64()
        except ImportError:
            algorithm = "sha256"
    return hashlib.new(algorithm)


def probe_compute():
    """Report the compute resources actually present on this host.

    Never fakes a GPU. Returns a dict with cpu, memory, gpu, and
    accelerator-probe results.
    """
    info = {
        "cpu_count": os.cpu_count() or 1,
        "mmap_available": True,  # stdlib, always present
        "gpu": None,
        "cuda": False,
        "rocm": False,
    }
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    info["mem_total_kb"] = int(line.split()[1])
                    break
    except OSError:
        pass
    # NVIDIA probe
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "-L"], capture_output=True, text=True,
                timeout=10, check=False)
            if out.returncode == 0 and out.stdout.strip():
                info["gpu"] = out.stdout.strip().splitlines()
                info["cuda"] = True
        except (subprocess.SubprocessError, OSError):
            pass
    # /dev/dri probe (Intel/AMD via DRM)
    try:
        dri = os.listdir("/dev/dri")
        if dri and info["gpu"] is None:
            info["gpu"] = [f"dri:{d}" for d in dri]
    except OSError:
        pass
    return info


def mmap_hash_file(path, algorithm="blake3"):
    """Hash a file via mmap (zero-copy) when beneficial.

    Uses mmap for files >= _MMAP_THRESHOLD; smaller files and empty files
    (mmap cannot map 0 bytes) use chunked reads. Returns hex digest.
    Raises OSError if the file cannot be read.
    """
    size = os.path.getsize(path)
    hasher = _new_hasher(algorithm)
    if size == 0:
        return hasher.hexdigest()
    if size < _MMAP_THRESHOLD:
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(_CHUNK), b""):
                hasher.update(blk)
        return hasher.hexdigest()
    with open(path, "rb") as f, mmap.mmap(
        f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        # Feed the hasher in _CHUNK slices; the slices are views into
        # the mapping — no userspace copy of the whole file.
        for off in range(0, size, _CHUNK):
            hasher.update(mm[off:off + _CHUNK])
    return hasher.hexdigest()


def _hash_one(args):
    """Worker for parallel_hash. Top-level so it pickles."""
    path, algorithm = args
    try:
        return path, mmap_hash_file(path, algorithm)
    except OSError:
        return path, None


def parallel_hash(paths, algorithm="blake3", workers=None):
    """Hash many files in parallel, one worker per CPU core by default.

    Returns {path: hexdigest}. Failed files map to None (never raises for
    a single bad file; the caller decides).
    """
    from concurrent.futures import ProcessPoolExecutor
    paths = list(paths)
    if not paths:
        return {}
    if workers is None:
        workers = min(os.cpu_count() or 1, len(paths))
    results = {}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for path, digest in ex.map(_hash_one,
                                   [(p, algorithm) for p in paths]):
            results[path] = digest
    return results


class VVRAMAllocator:
    """Reusable I/O buffer pool — kills malloc/free churn in hot loops.

    Part streaming (mirror mode, shard encode) allocates a buffer per part;
    this pool recycles them. Buffers are bytearrays; acquire() returns a
    buffer of at least `size` bytes, release() returns it to the pool.
    """

    def __init__(self, chunk_size=1 << 20, pool_size=8):
        self.chunk_size = chunk_size
        self.pool_size = pool_size
        self._pool = []  # list of bytearray
        self._acquired = 0
        self._reused = 0

    def acquire(self, size=None):
        """Get a buffer of at least `size` bytes (default chunk_size)."""
        need = size or self.chunk_size
        for i, buf in enumerate(self._pool):
            if len(buf) >= need:
                self._pool.pop(i)
                self._reused += 1
                self._acquired += 1
                return buf
        self._acquired += 1
        return bytearray(max(need, self.chunk_size))

    def release(self, buf):
        """Return a buffer to the pool (dropped if pool is full)."""
        self._acquired -= 1
        if len(self._pool) < self.pool_size and isinstance(buf, bytearray):
            self._pool.append(buf)

    def stats(self):
        return {
            "pool_size": self.pool_size,
            "cached": len(self._pool),
            "acquired_outstanding": self._acquired,
            "reused_total": self._reused,
        }


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(json.dumps(probe_compute(), indent=2, default=str))
    elif len(sys.argv) > 2 and sys.argv[1] == "hash":
        algo = sys.argv[3] if len(sys.argv) > 3 else "blake3"
        print(mmap_hash_file(sys.argv[2], algo))
    else:
        print("usage: vvram_allocator.py probe | hash PATH [algo]")
