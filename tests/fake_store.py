"""Test helpers for the CloudDog suite: FakeStore and config writer.

FakeStore is an in-memory :class:`clouddog.stores.Store` backend. It reads
the source file from disk and keeps blobs in a dict -- no network, no
credentials, no side effects beyond the test's tmp dir.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from clouddog import stores as stores_mod
from clouddog.core import inventory


class FakeStore(stores_mod.Store):
    """In-memory Store backend for tests."""

    name = "fake"

    def __init__(self, config: dict | None = None) -> None:
        super().__init__(config or {})
        self.blobs: dict[str, tuple[bytes, str]] = {}

    @staticmethod
    def _remote_path(remote_dir: str, remote_name: str | None, local_path: str) -> str:
        name = remote_name or Path(local_path).name
        return f"{remote_dir.rstrip('/')}/{name}" if remote_dir else name

    def upload(
        self,
        local_path: str,
        remote_dir: str = "",
        remote_name: str | None = None,
    ) -> stores_mod.StoredRef:
        """Store the file bytes in memory and return a StoredRef."""
        data = Path(local_path).read_bytes()
        remote_path = self._remote_path(remote_dir, remote_name, local_path)
        digest = hashlib.sha256(data).hexdigest()
        self.blobs[remote_path] = (data, digest)
        return stores_mod.StoredRef(
            store=self.name,
            remote_path=remote_path,
            size=len(data),
            sha256=digest,
            uri=f"fake://{remote_path}",
        )

    def verify(self, ref: stores_mod.StoredRef) -> bool:
        """True only when the stored bytes match ref size and sha256."""
        entry = self.blobs.get(ref.remote_path)
        return bool(entry) and entry[1] == ref.sha256 and len(entry[0]) == ref.size

    def exists(self, remote_dir: str, remote_name: str) -> stores_mod.StoredRef | None:
        """Return a StoredRef for a stored object, or None."""
        remote_path = self._remote_path(remote_dir, remote_name, remote_name)
        entry = self.blobs.get(remote_path)
        if entry is None:
            return None
        data, digest = entry
        return stores_mod.StoredRef(
            store=self.name,
            remote_path=remote_path,
            size=len(data),
            sha256=digest,
            uri=f"fake://{remote_path}",
        )


def write_config(tmp_path: Path, **overrides: object) -> str:
    """Write a minimal clouddog.yaml in *tmp_path* and return its path."""
    data: dict = {
        "stores": {"terabox": {}},
        "scan_roots": [str(tmp_path)],
        "journal_path": str(tmp_path / "journal.jsonl"),
        "terabox_remote_root": "/CloudDog",
    }
    data.update(overrides)
    cfg_path = tmp_path / "clouddog.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(cfg_path)


def scan_one(monkeypatch, path, kind="media-file", size=4096):
    """Make inventory.scan return exactly one candidate for *path*."""
    cand = inventory.Candidate(path=str(path), size_bytes=size, mtime=0.0, kind=kind)
    monkeypatch.setattr(inventory, "scan", lambda root, **kwargs: [cand])
    return cand
