"""Tests for clouddog_fetch: zip-range planning, extraction, mirrors.

Pure-logic tests: a synthetic zip is served through a read_range callback,
exactly like the selective torrent layer serves sparse-file ranges.
No network, no libtorrent needed.
"""

import hashlib
import io
import os
import struct
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import clouddog_fetch as cf


def build_zip():
    """A zip with a stored entry, a deflated entry, and nested paths."""
    payload = {
        "Documents/Gym Partner/big.psd": os.urandom(5000),
        "Documents/Gym Partner/small.jpg": b"\xff\xd8" + b"x" * 150,
        "Documents/other.txt": b"hello world" * 40,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "Documents/Gym Partner/big.psd",
            payload["Documents/Gym Partner/big.psd"],
            compress_type=zipfile.ZIP_STORED,
        )
        z.writestr(
            "Documents/Gym Partner/small.jpg",
            payload["Documents/Gym Partner/small.jpg"],
            compress_type=zipfile.ZIP_DEFLATED,
        )
        z.writestr(
            "Documents/other.txt",
            payload["Documents/other.txt"],
            compress_type=zipfile.ZIP_DEFLATED,
        )
    return buf.getvalue(), payload


class RangeSource:
    def __init__(self, data):
        self.data = data

    def __call__(self, start, end):
        return self.data[start : end + 1]


def test_eocd_and_cd_roundtrip():
    blob, payload = build_zip()
    src = RangeSource(blob)
    s, e = cf.find_eocd_range(len(blob))
    cd_off, cd_size, cd_count, z64 = cf.parse_eocd(src(s, e), len(blob))
    assert z64 is None and cd_count == 3
    entries = cf.parse_central_directory(src(cd_off, cd_off + cd_size - 1))
    assert set(entries) == set(payload)
    for name, data in payload.items():
        ent = entries[name]
        assert ent["uncomp_size"] == len(data)
        assert ent["crc32"] == zlib.crc32(data) & 0xFFFFFFFF


def test_extract_entries_verify():
    blob, payload = build_zip()
    src = RangeSource(blob)
    s, e = cf.find_eocd_range(len(blob))
    cd_off, cd_size, _, _ = cf.parse_eocd(src(s, e), len(blob))
    entries = cf.parse_central_directory(src(cd_off, cd_off + cd_size - 1))
    for name, want in payload.items():
        data, sha = cf.extract_entry(src, entries[name])
        assert data == want
        assert sha == hashlib.sha256(want).hexdigest()


def test_extract_detects_corruption():
    blob, payload = build_zip()
    bad = bytearray(blob)
    pos = blob.find(payload["Documents/Gym Partner/big.psd"][:64])
    assert pos > 0
    bad[pos + 30] ^= 0xFF
    src = RangeSource(bytes(bad))
    s, e = cf.find_eocd_range(len(bad))
    cd_off, cd_size, _, _ = cf.parse_eocd(src(s, e), len(bad))
    entries = cf.parse_central_directory(src(cd_off, cd_off + cd_size - 1))
    try:
        cf.extract_entry(src, entries["Documents/Gym Partner/big.psd"])
        raise AssertionError("corrupt entry extracted without error")
    except cf.FetchError:
        pass


def test_suffix_resolution_and_plan():
    blob, _payload = build_zip()
    src = RangeSource(blob)
    s, e = cf.find_eocd_range(len(blob))
    cd_off, cd_size, _, _ = cf.parse_eocd(src(s, e), len(blob))
    entries = cf.parse_central_directory(src(cd_off, cd_off + cd_size - 1))
    plan = cf.plan_zip_fetch(len(blob), entries, ["Gym Partner/big.psd", "Gym Partner/small.jpg"])
    assert set(plan["resolved"]) == {"Gym Partner/big.psd", "Gym Partner/small.jpg"}
    # every resolved entry's data range is inside the planned ranges
    for ent in plan["resolved"].values():
        lh = src(ent["local_header_offset"], ent["local_header_offset"] + 29)
        ds, de = cf.entry_data_range(lh, ent)
        assert any(rs <= ds and de <= re for rs, re in plan["stage2_ranges"])


def test_range_math():
    assert cf.merge_ranges([(10, 20), (0, 5), (6, 9), (30, 30)]) == [(0, 20), (30, 30)]
    assert cf.ranges_to_pieces([(0, 99), (250, 260)], 100, 10) == {0, 2}
    assert cf.ranges_to_pieces([], 100, 10) == set()


def test_zip64_cd_extra_field():
    # hand-built CD record whose sizes/lho live in the zip64 extra field
    name = b"a/b.bin"
    extra = struct.pack("<HHQQQ", 0x0001, 24, 123456789, 987654321, 555)
    rec = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            45,
            45,
            0,
            0,
            0,
            0,
            0xDEADBEEF,
            0xFFFFFFFF,
            0xFFFFFFFF,
            len(name),
            len(extra),
            0,
            0,
            0,
            0,
            0xFFFFFFFF,
        )
        + name
        + extra
    )
    entries = cf.parse_central_directory(rec)
    ent = entries["a/b.bin"]
    assert ent["uncomp_size"] == 123456789
    assert ent["comp_size"] == 987654321
    assert ent["local_header_offset"] == 555
    assert ent["crc32"] == 0xDEADBEEF


def test_want_manifest(tmp_path):
    p = tmp_path / "want.tsv"
    p.write_text(
        "150434\tGym Partner/Gym Partner/small.jpg\n"
        "# comment\n"
        "\n"
        "1227118\tGym Partner/Gym Partner/big.psd\n"
    )
    wants = cf.read_want_manifest(str(p))
    assert wants == [
        {"size": 150434, "path": "Gym Partner/Gym Partner/small.jpg"},
        {"size": 1227118, "path": "Gym Partner/Gym Partner/big.psd"},
    ]


def test_proxy_settings_from_env():
    env = {"http_proxy": "http://user:p%40ss@proxy.local:3128"}
    s = cf.proxy_settings_from_env(env)
    assert s["proxy_hostname"] == "proxy.local" and s["proxy_port"] == 3128
    assert s["proxy_username"] == "user" and s["proxy_password"] == "p@ss"
    assert s["proxy_tracker_connections"] is True
    assert s["proxy_peer_connections"] is False
    assert cf.proxy_settings_from_env({}) == {}


def test_mirrors():
    entry = {"sha256": "ab" * 32, "size": 42, "mirrors": []}
    m = cf.make_mirror("github", repo="o/r", release="v1", asset="a b.psd")
    assert m["url"] == "https://github.com/o/r/releases/download/v1/a%20b.psd"
    assert "gh release download v1 --repo o/r" in m["pull_back"]
    assert cf.add_mirror(entry, m) is True
    assert cf.add_mirror(entry, dict(m)) is False  # dedupe
    m2 = cf.make_mirror("github", repo="o/r", release="v2", asset="a b.psd")
    assert cf.add_mirror(entry, m2) is True
    assert len(entry["mirrors"]) == 2
