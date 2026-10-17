"""Tests for the aria2 direct-mirror wiring (mirror --direct).

Covers the binary probe, the local-file part splitter, and the
aria2 fetch function's fail-closed behavior. No network is used;
the live transport is smoke-tested manually (see docs/PULL-LOG.md).
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cloud_dog


def test_aria2c_binary_probes_known_install():
    # On this machine aria2 1.35.0 lives at ~/workspace/tools/aria2/aria2c.
    # Probe must either find a real executable or fail closed with None.
    found = cloud_dog._aria2c_binary()
    assert found is None or (
        os.path.isfile(found) and os.access(found, os.X_OK))


def test_split_file_to_parts_round_trip(tmp_path):
    src = tmp_path / "source.bin"
    data = bytes((i * 7 + 3) % 256 for i in range(2_500_000))
    src.write_bytes(data)
    staging = str(tmp_path / "staging")
    part_bytes = 1 << 20

    parts = list(cloud_dog.split_file_to_parts(
        str(src), staging, part_bytes=part_bytes, chunk=256 << 10))

    assert len(parts) == 3
    idxs, sizes, _digests = zip(
        *[(p[0], p[2], p[3]) for p in parts])
    assert list(idxs) == [0, 1, 2]
    assert sum(sizes) == len(data)
    # every part is within [part_bytes, part_bytes + chunk] bounds,
    # matching stream_url_to_parts' rolling-part contract
    for s in sizes[:-1]:
        assert part_bytes <= s <= part_bytes + (256 << 10)

    # reassembled content is byte-identical; per-part digests verify
    reassembled = b""
    for idx, ppath, size, digest in parts:
        with open(ppath, "rb") as f:
            blob = f.read()
        assert len(blob) == size
        assert hashlib.sha256(blob).hexdigest() == digest
        reassembled += blob
    assert hashlib.sha256(reassembled).hexdigest() == \
        hashlib.sha256(data).hexdigest()


def test_split_file_to_parts_exact_multiple(tmp_path):
    src = tmp_path / "exact.bin"
    data = b"A" * (1 << 20) + b"B" * (1 << 20)
    src.write_bytes(data)
    staging = str(tmp_path / "staging")

    parts = list(cloud_dog.split_file_to_parts(
        str(src), staging, part_bytes=1 << 20, chunk=256 << 10))

    assert len(parts) == 2
    assert sum(p[2] for p in parts) == len(data)
    body = b""
    for p in parts:
        with open(p[1], "rb") as f:
            body += f.read()
    assert body == data


def test_split_file_to_parts_hasher_factory(tmp_path):
    # main's stream_url_to_parts gained hasher_factory (blake3) in PR #22;
    # split_file_to_parts must accept the same opt-in.
    class FakeHasher:
        def __init__(self):
            self.buf = b""

        def update(self, data):
            self.buf += data

        def hexdigest(self):
            return "fake-" + str(len(self.buf))

    src = tmp_path / "hf.bin"
    src.write_bytes(b"X" * 100)
    parts = list(cloud_dog.split_file_to_parts(
        str(src), str(tmp_path / "staging"), part_bytes=64,
        hasher_factory=FakeHasher))
    assert parts
    assert all(p[3].startswith("fake-") for p in parts)


def test_aria2c_fetch_unknown_kind_fails_closed(tmp_path):
    ok, detail = cloud_dog._aria2c_fetch(
        "/nonexistent/aria2c", "bogus-kind", "x", str(tmp_path))
    assert not ok
    assert "unknown aria2 fetch kind" in detail


def test_aria2c_fetch_missing_binary_fails_closed(tmp_path):
    ok, detail = cloud_dog._aria2c_fetch(
        "/nonexistent/aria2c", "url", "https://example.com/",
        str(tmp_path))
    assert not ok
    assert "aria2c failed to start" in detail


def test_check_free_space_honors_margin(tmp_path, monkeypatch):
    class FakeUsage:
        free = 100

    monkeypatch.setattr(cloud_dog.shutil, "disk_usage",
                        lambda p: FakeUsage())
    ok, detail = cloud_dog._check_free_space(str(tmp_path), 50)
    assert not ok
    assert "not enough free space" in detail

    class FakeUsageBig:
        free = 10 * (1 << 30)

    monkeypatch.setattr(cloud_dog.shutil, "disk_usage",
                        lambda p: FakeUsageBig())
    ok, detail = cloud_dog._check_free_space(str(tmp_path), 1 << 30)
    assert ok


def test_head_content_length_returns_none_on_failure():
    # no server at this address -> fail-closed None, no exception
    assert cloud_dog._head_content_length(
        "http://127.0.0.1:9/nope", timeout=2) is None
