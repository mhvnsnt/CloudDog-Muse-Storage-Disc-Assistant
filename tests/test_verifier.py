"""Tests for clouddog.core.verifier (sibling-owned)."""

from clouddog.core import verifier

ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_sha256_known_vector(tmp_path):
    target = tmp_path / "abc.txt"
    target.write_bytes(b"abc")
    assert verifier.sha256_file(str(target)) == ABC_SHA256


def test_sha256_chunked_matches_default(tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"0123456789abcdef" * 65536)  # 1 MiB
    assert verifier.sha256_file(str(target), chunk_size=1024) == verifier.sha256_file(str(target))


def test_file_size(tmp_path):
    target = tmp_path / "sized.bin"
    target.write_bytes(b"z" * 1234)
    assert verifier.file_size(str(target)) == 1234
