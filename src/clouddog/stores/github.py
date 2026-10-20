"""GitHub Releases store backend.

Uploads via the ``gh`` CLI only (no ``requests``, no API tokens in-process).
GitHub release assets cap at 2 GB, so files larger than that are split with
``split -b 2000000000`` into ``<base>.part-aa`` … pieces, each uploaded as its
own asset and re-concatenated during verification.

Verification is byte-exact: after upload the backend re-downloads every
asset with ``gh release download`` and sha256-compares against the local
file (concatenated for split uploads). Size is also compared against the
release API asset record. Any mismatch raises :class:`VerificationFailed`
and nothing is deleted (this backend never deletes local files anyway).

remote_ref format: ``owner/repo@tag`` (asset name defaults to basename).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from .base import Health, HealthStatus, StoreBackend, StoredRef
from ..errors import CloudDogError, StoreDegraded, VerificationFailed

#: GitHub release asset size ceiling (2 GiB).
MAX_ASSET_BYTES = 2_000_000_000
#: Size of each piece when a file must be split for the 2 GiB asset cap.
SPLIT_BYTES = 2_000_000_000

_MAX_RETRIES = 3
_RETRY_BACKOFF_S = 2.0
_MAX_ERR_LEN = 500

_REF_RE = re.compile(r"^(?P<repo>[^/\s@]+/[^/\s@]+)@(?P<tag>[^/\s@]+)$")

# Substrings in gh stderr that indicate a transient failure worth retrying.
_TRANSIENT_HINTS = (
    "rate limit",
    "secondary rate",
    "temporarily",
    "timeout",
    "timed out",
    "connection",
    "network",
    "eof",
    "502",
    "503",
    "504",
    "socket",
)


def _truncate(text: str, limit: int = _MAX_ERR_LEN) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scratch_tmpdir(prefix: str) -> tempfile.TemporaryDirectory:
    """Scratch space for release downloads/verify. NEVER /tmp: it is a
    512MB tmpfs that fills independently of the disk, which made verify
    downloads fail with 'no space left on device' mid-tick."""
    base = os.path.join(os.path.expanduser("~/.clouddog"), "tmp")
    os.makedirs(base, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=base)


class GitHubReleaseBackend(StoreBackend):
    """Store backend backed by GitHub release assets via the ``gh`` CLI."""

    name = "github"

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _parse_ref(self, remote_ref: str) -> tuple[str, str]:
        """Split ``owner/repo@tag``. Raises CloudDogError on bad format."""
        m = _REF_RE.match((remote_ref or "").strip())
        if not m:
            raise CloudDogError(f"github remote_ref must be 'owner/repo@tag', got {remote_ref!r}")
        return m.group("repo"), m.group("tag")

    def _run_gh(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        """Run ``gh`` with retry on transient failures.

        Raises:
            CloudDogError: on permanent failures (after retries) and on
                transport problems (missing binary, timeouts).
        """
        last: subprocess.CompletedProcess | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                proc = subprocess.run(
                    ["gh", *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except FileNotFoundError as exc:
                raise CloudDogError("gh CLI not found on PATH") from exc
            except subprocess.TimeoutExpired as exc:
                last = None
                if attempt == _MAX_RETRIES - 1:
                    raise CloudDogError(
                        f"gh {' '.join(args[:2])} timed out after {timeout}s"
                    ) from exc
                time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
                continue
            if proc.returncode == 0:
                return proc
            last = proc
            err = (proc.stderr or "").lower()
            transient = any(hint in err for hint in _TRANSIENT_HINTS)
            if not transient or attempt == _MAX_RETRIES - 1:
                raise CloudDogError(f"gh {' '.join(args[:2])} failed: {_truncate(proc.stderr)}")
            time.sleep(_RETRY_BACKOFF_S * (attempt + 1))
        # Unreachable, but keeps the type checker honest.
        assert last is not None
        raise CloudDogError(f"gh failed: {_truncate(last.stderr)}")

    def _ensure_release(self, repo: str, tag: str) -> None:
        """Create the release if ``gh release view`` says it is missing."""
        try:
            self._run_gh(["release", "view", "--repo", repo, tag])
        except CloudDogError:
            pass  # missing or otherwise unreadable -> try to create
        else:
            return
        try:
            self._run_gh(
                [
                    "release",
                    "create",
                    tag,
                    "--repo",
                    repo,
                    "--title",
                    tag,
                    "--notes",
                    "clouddog v1",
                ]
            )
        except CloudDogError as exc:
            raise CloudDogError(f"could not ensure release {repo}@{tag}: {exc}") from exc

    def _split(self, local_path: str, tmpdir: str) -> list[str]:
        """Split into <=2 GiB pieces named ``<base>.part-aa`` …. Returns paths."""
        base = os.path.basename(local_path)
        prefix = os.path.join(tmpdir, base + ".part-")
        proc = subprocess.run(
            ["split", "-b", str(SPLIT_BYTES), local_path, prefix],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if proc.returncode != 0:
            raise CloudDogError(f"split failed: {_truncate(proc.stderr)}")
        parts = sorted(
            os.path.join(tmpdir, p) for p in os.listdir(tmpdir) if p.startswith(base + ".part-")
        )
        if not parts:
            raise CloudDogError("split produced no parts")
        return parts

    def _asset_size(self, repo: str, tag: str, asset_name: str) -> int | None:
        """Return the size recorded by the release API for an asset, or None."""
        for asset in self._list_assets(repo, tag):
            if asset.get("name") == asset_name:
                return int(asset.get("size", -1))
        return None

    def _list_assets(self, repo: str, tag: str) -> list[dict]:
        """Raw asset dicts the release API reports for ``repo@tag``."""
        proc = self._run_gh(["api", f"repos/{repo}/releases"], timeout=120)
        try:
            releases = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise CloudDogError(
                f"could not parse gh api releases output: {_truncate(str(exc))}"
            ) from exc
        for release in releases:
            if release.get("tag_name") == tag:
                return list(release.get("assets", []))
        return []

    def _resolve_asset_name(self, repo: str, tag: str, base: str) -> str:
        """The asset's ACTUAL name on the release after an upload.

        ``gh release upload`` renames some files (dotfiles gain a
        ``default.`` prefix). Verifying under the pre-upload name would
        look for an asset that does not exist, so resolve the real name:
        exact match first, then the known ``default.`` form, then a
        unique suffix match. Raises when it cannot be pinned down.
        """
        names = [str(a.get("name", "")) for a in self._list_assets(repo, tag)]
        if base in names:
            return base
        prefixed = f"default.{base}"
        if prefixed in names:
            return prefixed
        suffix = [n for n in names if n.endswith(base)]
        if len(suffix) == 1:
            return suffix[0]
        raise CloudDogError(
            f"could not resolve uploaded asset {base!r} on {repo}@{tag}: release assets are {names}"
        )

    def _download_asset(self, repo: str, tag: str, asset_name: str, destdir: str) -> str:
        """Download one asset into destdir. Returns the local path."""
        self._run_gh(
            [
                "release",
                "download",
                tag,
                "--repo",
                repo,
                "-p",
                asset_name,
                "-D",
                destdir,
            ],
            timeout=3600,
        )
        path = os.path.join(destdir, asset_name)
        if not os.path.isfile(path):
            raise CloudDogError(f"gh downloaded asset {asset_name} but it is missing on disk")
        return path

    def _remote_sha_matches(
        self, repo: str, tag: str, asset_name: str, expected_sha256: str
    ) -> bool:
        """True iff the existing remote asset downloads sha256-identical."""
        with _scratch_tmpdir("clouddog-gh-reuse-") as tmpdir:
            path = self._download_asset(repo, tag, asset_name, tmpdir)
            digest = hashlib.sha256()
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest() == expected_sha256

    def _verify_assets(
        self, repo: str, tag: str, asset_names: list[str], expected_sha256: str
    ) -> None:
        """Re-download every asset, sha256-compare, raise VerificationFailed.

        For multi-asset (split) uploads the parts are concatenated in sorted
        order before hashing.
        """
        with _scratch_tmpdir("clouddog-gh-verify-") as tmpdir:
            downloaded: list[str] = []
            for asset_name in asset_names:
                recorded = self._asset_size(repo, tag, asset_name)
                path = self._download_asset(repo, tag, asset_name, tmpdir)
                if recorded is not None and os.path.getsize(path) != recorded:
                    raise VerificationFailed(
                        f"asset {asset_name}: downloaded size "
                        f"{os.path.getsize(path)} != recorded size {recorded}"
                    )
                downloaded.append(path)
            digest = hashlib.sha256()
            for path in sorted(downloaded):
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
                        digest.update(chunk)
            if digest.hexdigest() != expected_sha256:
                raise VerificationFailed(
                    f"sha256 mismatch for {repo}@{tag}: "
                    f"stored {expected_sha256[:16]}… vs downloaded {digest.hexdigest()[:16]}…"
                )

    # ------------------------------------------------------------------ #
    # StoreBackend contract
    # ------------------------------------------------------------------ #
    def health(self) -> HealthStatus:
        try:
            proc = subprocess.run(
                ["gh", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return HealthStatus(Health.DOWN, "gh CLI not found on PATH")
        except subprocess.TimeoutExpired:
            return HealthStatus(Health.DOWN, "gh auth status timed out")
        if proc.returncode == 0:
            return HealthStatus(Health.LIVE)
        return HealthStatus(Health.DOWN, f"gh not authenticated: {_truncate(proc.stderr)}")

    def store(self, local_path: str, remote_ref: str, dry_run: bool = False) -> StoredRef:
        repo, tag = self._parse_ref(remote_ref)
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        size = os.path.getsize(local_path)
        sha256 = _sha256_of(local_path)
        base = os.path.basename(local_path)

        if dry_run:
            return StoredRef(
                backend=self.name,
                locator=f"dry-run:{repo}@{tag}/{base}",
                size=size,
                sha256=sha256,
                extra={"parts": 1, "dry_run": True},
            )

        status = self.health()
        if status.health is not Health.LIVE:
            raise StoreDegraded(self.name, status.reason or "gh not authenticated")

        self._ensure_release(repo, tag)

        if size > MAX_ASSET_BYTES:
            with _scratch_tmpdir("clouddog-gh-split-") as tmpdir:
                parts = self._split(local_path, tmpdir)
                asset_names = [os.path.basename(p) for p in parts]
                self._run_gh(
                    ["release", "upload", "--repo", repo, tag, *parts, "--clobber"],
                    timeout=7200,
                )
                # gh may rename uploads (dotfiles); resolve actual names.
                asset_names = [self._resolve_asset_name(repo, tag, n) for n in asset_names]
                # Verify while the temp parts still exist.
                self._verify_assets(repo, tag, asset_names, sha256)
            locator = f"{repo}@{tag}/{base}"
            extra = {"parts": len(asset_names), "asset_names": asset_names}
        else:
            asset_names = [base]
            # L5: never silently clobber a same-named asset from another
            # lane/file. If it exists and is byte-identical, reuse it;
            # if it differs, refuse. (Check the gh-renamed form too.)
            existing = self._asset_size(repo, tag, base)
            existing_name = base
            if existing is None:
                existing = self._asset_size(repo, tag, f"default.{base}")
                if existing is not None:
                    existing_name = f"default.{base}"
            if existing is not None:
                if existing == size and self._remote_sha_matches(repo, tag, existing_name, sha256):
                    locator = f"{repo}@{tag}/{base}"
                    return StoredRef(
                        backend=self.name,
                        locator=locator,
                        size=size,
                        sha256=sha256,
                        extra={"parts": 1, "asset_names": [existing_name], "reused": True},
                    )
                raise CloudDogError(
                    f"asset {base!r} already exists on {repo}@{tag} with "
                    f"different content; refusing to clobber"
                )
            self._run_gh(
                ["release", "upload", "--repo", repo, tag, local_path, "--clobber"],
                timeout=7200,
            )
            # gh may rename the upload (dotfiles gain a 'default.' prefix);
            # verify under the actual asset name, not the pre-upload name.
            asset_names = [self._resolve_asset_name(repo, tag, base)]
            self._verify_assets(repo, tag, asset_names, sha256)
            locator = f"{repo}@{tag}/{base}"
            extra = {"parts": 1, "asset_names": asset_names}

        return StoredRef(
            backend=self.name,
            locator=locator,
            size=size,
            sha256=sha256,
            extra=extra,
        )

    def verify(self, local_path: str, stored: StoredRef) -> bool:
        """Re-download asset(s) per the locator and sha256-compare.

        Returns False on mismatch; raises only on transport errors.
        """
        if not os.path.isfile(local_path):
            raise CloudDogError(f"local file not found: {local_path}")
        try:
            head, _, _ = stored.locator.rpartition("/")
            repo, tag = self._parse_ref(head)
        except CloudDogError as exc:
            raise CloudDogError(f"cannot parse stored locator {stored.locator!r}: {exc}") from exc
        asset_names = stored.extra.get("asset_names") or [stored.locator.rpartition("/")[2]]
        try:
            self._verify_assets(repo, tag, asset_names, _sha256_of(local_path))
        except VerificationFailed:
            return False
        return True
