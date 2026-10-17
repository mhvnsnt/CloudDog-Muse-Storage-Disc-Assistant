"""Google Drive store backend (via the ``rclone`` CLI).

Honest note: Drive is best-effort. The owner's Drive has been near-quota
before, so an upload failure here is always a StoreError and CloudDog must
never delete the local file when this store reports a failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import (
    Store,
    StoredRef,
    StoreError,
    StoreUnavailable,
    VerificationFailed,
    _redact,
    sha256_of,
)

UPLOAD_TIMEOUT = 600


class DriveStore(Store):
    """Google Drive backend via rclone."""

    name = "drive"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.remote = str(config.get("remote", ""))
        if not self.remote:
            raise StoreUnavailable("Drive store requires config key 'remote' (e.g. 'gdrive').")
        self.base_dir = str(config.get("base_dir", "clouddog")).strip("/") or "clouddog"
        if shutil.which("rclone") is None:
            raise StoreUnavailable("The 'rclone' CLI is not installed or not on PATH.")
        proc = self._rclone(["lsd", f"{self.remote}:"])
        if proc.returncode != 0:
            raise StoreUnavailable(
                f"rclone remote {self.remote!r} is not usable "
                f"({_redact(proc.stderr.strip())}). rclone remote setup is "
                "operator-done; see docs/OPERATIONS.md."
            )

    # -- rclone plumbing ----------------------------------------------------

    def _rclone(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["rclone", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError(f"'rclone {' '.join(args[:2])}' failed: {_redact(str(exc))}") from exc

    def _target(self, remote_dir: str, name: str) -> tuple[str, str]:
        """Return (remote_path, rclone target '<remote>:<path>')."""
        parts = [self.base_dir]
        cleaned = remote_dir.strip("/")
        if cleaned:
            parts.append(cleaned)
        parts.append(name)
        path = "/".join(parts)
        return path, f"{self.remote}:{path}"

    def _remote_size(self, path: str) -> int | None:
        """Byte size of a remote file via ``rclone lsjson``; None if absent."""
        proc = self._rclone(["lsjson", f"{self.remote}:{path}"])
        if proc.returncode != 0:
            return None
        try:
            info = json.loads(proc.stdout)
        except ValueError:
            return None
        if isinstance(info, list):  # a directory listing, not a file
            return None
        try:
            return int(info.get("Size", -1))
        except (TypeError, ValueError):
            return None

    # -- Store interface -----------------------------------------------------

    def upload(
        self,
        local_path: str,
        remote_dir: str = "",
        remote_name: str | None = None,
    ) -> StoredRef:
        if not os.path.isfile(local_path):
            raise StoreError(f"Local file not found: {local_path!r}")
        name = remote_name or os.path.basename(local_path)
        size = os.path.getsize(local_path)
        digest = sha256_of(local_path)
        path, target = self._target(remote_dir, name)
        proc = self._rclone(
            ["copyto", local_path, target, "--progress=false"], timeout=UPLOAD_TIMEOUT
        )
        if proc.returncode != 0:
            raise StoreError(f"'rclone copyto' failed for {name!r}: {_redact(proc.stderr.strip())}")
        ref = StoredRef(
            store="drive",
            remote_path=path,
            size=size,
            sha256=digest,
            uri=f"drive:{self.remote}:{path}",
        )
        if not self.verify(ref):
            raise VerificationFailed(f"Drive upload of {name!r} did not verify at {path!r}.")
        return ref

    def verify(self, ref: StoredRef) -> bool:
        """Re-check Drive NOW; True iff the file exists with matching byte size."""
        size = self._remote_size(ref.remote_path)
        return size is not None and size == ref.size

    def exists(self, remote_dir: str, remote_name: str) -> StoredRef | None:
        path, _ = self._target(remote_dir, remote_name)
        size = self._remote_size(path)
        if size is None:
            return None
        return StoredRef(
            store="drive",
            remote_path=path,
            size=size,
            sha256="",  # lsjson exposes no content hash
            uri=f"drive:{self.remote}:{path}",
        )

    def uri_for(self, ref: StoredRef) -> str:
        return f"drive:{self.remote}:{ref.remote_path}"
