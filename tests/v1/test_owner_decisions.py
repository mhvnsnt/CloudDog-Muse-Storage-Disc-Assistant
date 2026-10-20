"""Owner decisions 2026-10-19 ("Yes and yes for now"):

1. 100MB SINGLE STANDARD — one named constant (``CHUNK_BYTES`` in
   ``clouddog.stores.base``) used by EVERY chunking path: the GitHub
   backend store() split, the cloud_dog.py mirror part stream, and (when
   it lands on main) the straight-to-store streamer.
2. FULL RE-DOWNLOAD VERIFY, EVERY TIME — verification must re-download
   the stored bytes and hash-check them. The name+size / metadata
   shortcut is removed as a verification basis; backends that cannot
   produce bytes (TeraBox) FAIL CLOSED with the exact reason.
"""

from __future__ import annotations

import ast
import os

import pytest

from clouddog.errors import VerificationFailed
from clouddog.stores import github as github_mod
from clouddog.stores.base import CHUNK_BYTES
from clouddog.stores.github import GitHubReleaseBackend
from clouddog.stores.terabox import TeraBoxBackend

MB = 1024 * 1024

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# ------------------------------------------------------------------ #
# Decision 1: 100MB single standard
# ------------------------------------------------------------------ #
def test_chunk_bytes_is_100_mib():
    assert CHUNK_BYTES == 100 * MB


def test_github_split_bytes_is_the_shared_constant():
    assert github_mod.SPLIT_BYTES == CHUNK_BYTES
    assert github_mod.SPLIT_BYTES == 100 * MB


def test_github_split_produces_chunk_sized_parts(tmp_path, monkeypatch):
    """_split must read the shared constant, not a hardcoded size.

    SPLIT_BYTES is patched down to 4 MiB so the test stays tiny on disk
    (the real 100 MiB value is asserted separately above); the split of
    a 10 MiB + 123 file must then yield [4 MiB, 4 MiB, 2 MiB + 123].
    If _split ignored the constant, the parts would not match.
    """
    monkeypatch.setattr(github_mod, "SPLIT_BYTES", 4 * MB)
    target = tmp_path / "big.bin"
    size = 10 * MB + 123
    with open(target, "wb") as fh:
        fh.truncate(size)  # sparse source; parts are small real files
    parts = GitHubReleaseBackend()._split(str(target), str(tmp_path))
    sizes = [os.path.getsize(p) for p in parts]
    assert sizes == [4 * MB, 4 * MB, 2 * MB + 123]


def test_cloud_dog_part_bytes_mirrors_the_shared_constant():
    """cloud_dog.py cannot import the src/ tree (root package shadows
    it), so its PART_BYTES mirrors CHUNK_BYTES — prove they are equal."""
    path = os.path.join(REPO_ROOT, "cloud_dog.py")
    with open(path) as fh:
        tree = ast.parse(fh.read())
    value = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "PART_BYTES":
                    # Evaluate our own source literal (``100 * 1024 * 1024``)
                    # with no builtins available.
                    value = eval(  # noqa: S307
                        compile(ast.Expression(node.value), "<PART_BYTES>", "eval"),
                        {"__builtins__": {}},
                    )
    assert value is not None, "PART_BYTES not found in cloud_dog.py"
    assert value == CHUNK_BYTES


def test_no_hardcoded_oversized_split_values_in_chunking_code():
    """No 2GiB / 500MB split literals may survive in chunking code."""
    import re

    offenders = []
    for rel in (
        "src/clouddog/stores/github.py",
        "src/clouddog/stores/base.py",
        "cloud_dog.py",
    ):
        with open(os.path.join(REPO_ROOT, rel)) as fh:
            src = fh.read()
        for pat in (r"split[^\n]*2_000_000_000", r"split[^\n]*500\s*\*\s*1024"):
            for m in re.finditer(pat, src):
                offenders.append(f"{rel}: {m.group(0)[:60]}")
    assert offenders == []


# ------------------------------------------------------------------ #
# Decision 2: full re-download verify; fail closed without byte access
# ------------------------------------------------------------------ #
def test_terabox_verify_fails_closed_with_reason(tmp_path):
    """TeraBox has no byte-level download API wired: verify() must NOT
    pass on name+size — it raises VerificationFailed (fail closed)."""
    local = tmp_path / "f.bin"
    local.write_bytes(b"x" * 10)
    from clouddog.stores.base import StoredRef

    ref = StoredRef(backend="terabox", locator="/dir/f.bin", size=10, sha256="0" * 64)
    with pytest.raises(VerificationFailed) as exc:
        TeraBoxBackend().verify(str(local), ref)
    assert "fail closed" in str(exc.value)
