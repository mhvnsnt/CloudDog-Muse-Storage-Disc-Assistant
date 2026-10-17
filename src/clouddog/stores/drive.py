"""Google Drive store backend via ``rclone`` subprocess.

Uses ``rclone copyto`` with ``--checksum`` for the transfer and
``rclone check --one-way`` for byte-exact verification before returning.

remote_ref format: ``remote:path`` (rclone remote name + path, e.g.
``drive:backups/file.bin``). The default remote is taken from the
``CLOUDEDOG_DRIVE_REMOTE`` env var (e.g. ``drive:``) when remote_ref
omits the remote name.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from .base import Health, HealthStatus, StoreBackend, StoredRef
from ..errors import CloudDogError, StoreDegraded

# CloudDog-managed rclone install (pulled in 2026-10-17 per owner order).
# Tests override this with monkeypatch to simulate "not installed".
MANAGED_RCLONE_BIN = os.path.expanduser("~/workspace/tools/rclone/rclone")

_MAX_ERR_LEN = 500


def _truncate(text: str, limit: int = _MAX_ERR_LEN) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DriveBackend(StoreBackend):
    """Store backend backed by any rclone remote (Drive by default)."""

    name = "drive"

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _default_remote(self) -> str:
        return os.environ.get("CLOUDEDOG_DRIVE_REMOTE", "drive:")

    def _parse_ref(self, remote_ref: str) -> str:
        """Resolve remote_ref to a full ``remote:path`` target.

        Accepts ``remote:path`` as-is, or a bare ``path`` which is joined to
        the default remote. Raises CloudDogError on an empty ref.
        """
        ref = (remote_ref or "").strip()
        if not ref:
            raise CloudDogError("drive remote_ref must be 'remote:path', got empty")
        if ":" in ref:
            return ref
        return self._default_remote().rstrip(":") + ":" + ref.lstrip("/")

    def _rclone_bin(self) -> str | None:
        """Resolve the rclone binary: env override, then the CloudDog-managed
        install at ~/workspace/tools/rclone/rclone, then PATH."""
        env_bin = os.environ.get("CLOUDEDOG_RCLONE_BIN")
        if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return env_bin
        if os.path.isfile(MANAGED_RCLONE_BIN) and os.access(MANAGED_RCLONE_BIN, os.X_OK):
            return MANAGED_RCLONE_BIN
        return shutil.which("rclone")

    def _run_rclone(self, args: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
        """Run ``rclone``. Raises CloudDogError on transport problems."""
        exe = self._rclone_bin()
        if exe is None:
            raise CloudDogError("rclone not found (set CLOUDEDOG_RCLONE_BIN)")
        try:
            return subprocess.run(
                [exe, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise CloudDogError("rclone not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise CloudDogError(
                f"rclone {' '.join(args[:2])} timed out after {timeout}s"
            ) from exc

    # ------------------------------------------------------------------ #
    # StoreBackend contract
    # ------------------------------------------------------------------ #
    def health(self) -> HealthStatus:
        if self._rclone_bin() is None:
            return HealthStatus(Health.DEGRADED, "rclone not installed")
        try:
            proc = self._run_rclone(
                ["lsd", self._default_remote(), "--max-depth", "1"], timeout=120
            )
        except CloudDogError as exc:
            return HealthStatus(Health.DEGRADED, str(exc))
        if proc.returncode == 0:
            return HealthStatus(Health.LIVE)
        return HealthStatus(
            Health.DEGRADED, _truncate(proc.stderr or proc.stdout)
        )

    def store(
        self, local_path: str, remote_ref: str, dry_run: bool = False
    ) -> StoredRef:
        target = self._parse_ref(remote_ref)
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        size = os.path.getsize(local_path)
        sha256 = _sha256_of(local_path)

        if dry_run:
            return StoredRef(
                backend=self.name,
                locator=f"dry-run:{target}",
                size=size,
                sha256=sha256,
                extra={"dry_run": True},
            )

        if self._rclone_bin() is None:
            raise StoreDegraded(self.name, "rclone not installed")

        proc = self._run_rclone(["copyto", local_path, target, "--checksum"])
        if proc.returncode != 0:
            raise CloudDogError(
                f"rclone copyto failed: {_truncate(proc.stderr or proc.stdout)}"
            )

        check = self._run_rclone(["check", local_path, target, "--one-way"])
        if check.returncode != 0:
            raise CloudDogError(
                f"rclone check failed — remote copy not byte-identical: "
                f"{_truncate(check.stderr or check.stdout)}"
            )

        return StoredRef(
            backend=self.name,
            locator=target,
            size=size,
            sha256=sha256,
            extra={"verify": "rclone check --one-way (byte-identical)"},
        )

    def verify(self, local_path: str, stored: StoredRef) -> bool:
        """Re-run ``rclone check --one-way`` against the stored target.

        Returns False when the check reports differences; raises only on
        transport errors.
        """
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        locator = stored.locator
        if locator.startswith("dry-run:"):
            locator = locator[len("dry-run:") :]
        proc = self._run_rclone(["check", local_path, locator, "--one-way"])
        if proc.returncode == 0:
            return True
        if proc.returncode == 1:
            # rclone check exits 1 when differences are found — a plain
            # mismatch, not a transport error.
            return False
        # L1: exit >1 is a transport/config error — raise, don't misreport
        # it as "verification failed".
        raise CloudDogError(
            f"rclone check failed (exit {proc.returncode}): "
            f"{_truncate(proc.stderr or proc.stdout)}"
        )
