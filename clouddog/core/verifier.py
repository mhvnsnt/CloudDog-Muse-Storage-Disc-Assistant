"""Byte verification helpers: hashes and sizes used to prove a store copy."""

from __future__ import annotations

import hashlib
import os


def sha256_file(path: str, chunk_size: int = 4 * 1024 * 1024) -> str:
    """Return the hex SHA-256 digest of the file at *path*."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_size(path: str) -> int:
    """Return the size in bytes of the file at *path*."""
    return os.path.getsize(path)
