"""TeraBox store backend.

Wraps the real API uploader at
``~/workspace/tools/terabox-uploader/tbupload.py`` (see
``~/workspace/agent-ops/TERABOX-SESSION-KEEPER.md`` for the session-keeper
design). All work goes through subprocess calls to that script — this
module never touches credential values: it checks only for the existence
of ``.creds.json`` (chmod 600, gitignored) and lets ``tbupload.py`` do the
auth. Credential values are never printed, logged, or returned.

Verification honesty note: ``tbupload.py`` itself verifies each upload
with TeraBox's ``/api/list`` (name + byte size) after the create step.
Its ``ls`` output likewise carries name and size only — the TeraBox web
API path used here does not expose a remote sha256. This backend therefore
verifies by remote name + byte size, and computes sha256 of the LOCAL file
for the :class:`StoredRef` record (used for bookkeeping and for
name+size-based re-verification). The ``extra`` field documents this
explicitly so callers never mistake it for a remote-hash check.

remote_ref format: ``/remote/dir`` (remote directory; asset name defaults
to basename).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys

from .base import Health, HealthStatus, StoreBackend, StoredRef
from ..errors import CloudDogError, StoreDegraded

TB_UPLOADER = os.path.expanduser(
    "~/workspace/tools/terabox-uploader/tbupload.py"
)
CREDS_PATH = os.path.expanduser(
    "~/workspace/tools/terabox-uploader/.creds.json"
)

_MAX_ERR_LEN = 500
_HEALTH_TIMEOUT_S = 60


def _truncate(text: str, limit: int = _MAX_ERR_LEN) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class TeraBoxBackend(StoreBackend):
    """Store backend backed by TeraBox via the real ``tbupload.py`` CLI."""

    name = "terabox"

    @property
    def supports_content_verify(self) -> bool:
        # TeraBox's API exposes no remote checksum; verify() is name+size
        # only. Auto-delete paths must never clear local on this tier.
        return False

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _run_tb(self, args: list[str], timeout: int) -> subprocess.CompletedProcess:
        """Run ``tbupload.py``. Raises CloudDogError on transport problems."""
        try:
            return subprocess.run(
                [sys.executable, TB_UPLOADER, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise CloudDogError(f"tbupload.py not found at {TB_UPLOADER}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CloudDogError(
                f"tbupload.py {' '.join(args)} timed out after {timeout}s"
            ) from exc

    def _ls(self, remotedir: str) -> dict[str, int]:
        """List a remote dir via ``tbupload.py ls`` -> {name: size} (files only)."""
        proc = self._run_tb(["ls", remotedir], timeout=120)
        if proc.returncode != 0:
            raise CloudDogError(
                f"tbupload.py ls {remotedir} failed: {_truncate(proc.stderr or proc.stdout)}"
            )
        entries: dict[str, int] = {}
        for line in (proc.stdout or "").splitlines():
            line = line.rstrip("\n")
            if not line.startswith("f "):
                continue  # 'd' lines are directories; malformed lines are skipped
            try:
                _, size_s, name = line.split(None, 2)
                entries[name] = int(size_s)
            except (ValueError, IndexError):
                continue
        return entries

    def _ensure_live(self) -> None:
        status = self.health()
        if status.health is not Health.LIVE:
            raise StoreDegraded(self.name, status.reason or "tbupload.py not usable")

    # ------------------------------------------------------------------ #
    # StoreBackend contract
    # ------------------------------------------------------------------ #
    def health(self) -> HealthStatus:
        if not os.path.exists(TB_UPLOADER):
            return HealthStatus(Health.DOWN, f"tbupload.py missing at {TB_UPLOADER}")
        if not os.path.exists(CREDS_PATH):
            return HealthStatus(
                Health.DEGRADED,
                "no creds — run setup_creds.py via browser cookie flow",
            )
        try:
            proc = self._run_tb(["resolve"], timeout=_HEALTH_TIMEOUT_S)
        except CloudDogError as exc:
            return HealthStatus(Health.DEGRADED, str(exc))
        if proc.returncode == 0:
            return HealthStatus(Health.LIVE)
        return HealthStatus(
            Health.DEGRADED,
            _truncate(proc.stderr or proc.stdout or "tbupload.py resolve failed"),
        )

    def store(
        self, local_path: str, remote_ref: str, dry_run: bool = False
    ) -> StoredRef:
        remotedir = (remote_ref or "").strip().rstrip("/")
        if not remotedir.startswith("/"):
            raise CloudDogError(
                f"terabox remote_ref must be '/remote/dir', got {remote_ref!r}"
            )
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        size = os.path.getsize(local_path)
        sha256 = _sha256_of(local_path)
        base = os.path.basename(local_path)

        if dry_run:
            return StoredRef(
                backend=self.name,
                locator=f"dry-run:{remotedir}/{base}",
                size=size,
                sha256=sha256,
                extra={"dry_run": True},
            )

        self._ensure_live()

        # Generous timeout: ~5 min per 100 MB, minimum 10 minutes.
        timeout = max(600, int(size / (100 * 1024 * 1024)) * 300 + 300)
        proc = self._run_tb(["upload", local_path, remotedir], timeout=timeout)
        if proc.returncode != 0:
            raise CloudDogError(
                f"tbupload.py upload failed: {_truncate(proc.stderr or proc.stdout)}"
            )

        # Verify: tbupload.py itself already checked name+size via /api/list;
        # confirm the file shows up in the remote dir listing with the right size.
        entries = self._ls(remotedir)
        if entries.get(base) != size:
            raise CloudDogError(
                f"post-upload verify failed: {base!r} not in {remotedir} "
                f"with size {size} (remote: {entries.get(base)!r})"
            )

        return StoredRef(
            backend=self.name,
            locator=f"{remotedir}/{base}",
            size=size,
            sha256=sha256,
            extra={
                "verify": "name+size via tbupload.py ls; sha256 is of the local "
                "file only — tbupload.py does not expose a remote sha256",
            },
        )

    def verify(self, local_path: str, stored: StoredRef) -> bool:
        """Re-verify name+size against the remote dir listing.

        Returns False on mismatch; raises only on transport errors.
        """
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        locator = stored.locator
        if locator.startswith("dry-run:"):
            locator = locator[len("dry-run:") :]
        remotedir, _, base = locator.rpartition("/")
        if not remotedir.startswith("/") or not base:
            raise CloudDogError(
                f"cannot parse terabox stored locator {stored.locator!r}"
            )
        try:
            entries = self._ls(remotedir)
        except CloudDogError:
            raise
        return entries.get(base) == os.path.getsize(local_path)
