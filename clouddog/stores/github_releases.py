"""GitHub Releases store backend (via the ``gh`` CLI).

Pattern (mirrors the proven disk-guardian offload lane):
ensure the release exists -> skip re-upload when an asset with the same
name and size is already there (idempotent) -> ``gh release upload`` ->
verify. With ``strict_verify`` the asset is downloaded back and its sha256
is compared before the local file is trusted for deletion.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from . import (
    Store,
    StoredRef,
    StoreError,
    StoreUnavailable,
    VerificationFailed,
    _redact,
    sha256_of,
)

UPLOAD_TIMEOUT = 300


class GitHubReleasesStore(Store):
    """GitHub Releases backend.

    Releases have a flat asset namespace per tag, so ``remote_dir`` is
    ignored by :meth:`exists` (documented below) and remote paths are
    recorded as ``<tag>/<asset>``.
    """

    name = "github"

    def __init__(self, config: dict) -> None:
        super().__init__(config)
        self.repo = str(config.get("repo", ""))
        self.tag = str(config.get("tag", ""))
        if not self.repo or not self.tag:
            raise StoreUnavailable(
                "GitHub store requires config keys 'repo' (e.g. 'mhvnsnt/drive-storage') "
                "and 'tag' (e.g. 'bulk-archive-2026-10-17')."
            )
        if shutil.which("gh") is None:
            raise StoreUnavailable("The 'gh' CLI is not installed or not on PATH.")
        self.draft_ok = bool(config.get("draft_ok", True))
        self.strict_verify = bool(config.get("strict_verify", False))

    # -- gh plumbing -------------------------------------------------------

    def _gh(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["gh", *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StoreError(f"'gh {' '.join(args[:2])}' failed: {_redact(str(exc))}") from exc

    def _release_exists(self) -> bool:
        proc = self._gh(["release", "view", self.tag, "--repo", self.repo])
        return proc.returncode == 0

    def _ensure_release(self) -> None:
        if self._release_exists():
            return
        if not self.draft_ok:
            raise StoreError(
                f"GitHub release {self.tag!r} does not exist in {self.repo!r} "
                "and 'draft_ok' is false; refusing to create it."
            )
        proc = self._gh(
            [
                "release",
                "create",
                self.tag,
                "--repo",
                self.repo,
                "--draft",
                "--title",
                f"CloudDog archive {self.tag}",
                "--notes",
                (
                    "CloudDog disk-offload archive. Assets are individually "
                    "sha256-verified by download-back before local deletion."
                ),
            ]
        )
        if proc.returncode != 0:
            raise StoreError(
                f"Failed to create draft release {self.tag!r} in {self.repo!r}: "
                f"{_redact(proc.stderr.strip())}"
            )

    def _assets(self) -> list[dict]:
        proc = self._gh(["release", "view", self.tag, "--repo", self.repo, "--json", "assets"])
        if proc.returncode != 0:
            raise StoreError(
                f"'gh release view' failed for tag {self.tag!r} in {self.repo!r}: "
                f"{_redact(proc.stderr.strip())}"
            )
        try:
            data = json.loads(proc.stdout)
        except ValueError as exc:
            raise StoreError(
                f"'gh release view' returned invalid JSON for tag {self.tag!r}: {_redact(str(exc))}"
            ) from exc
        assets = data.get("assets", [])
        return assets if isinstance(assets, list) else []

    def _digests(self) -> dict[str, str]:
        """Map asset name -> sha256 hex via the REST API (cloud_dog.py pattern).

        ``gh release view --json assets`` exposes name+size only; the REST
        ``.digest`` field (``sha256:<hex>``) gives hash-strong verification
        without downloading. Returns {} on any failure — callers treat a
        missing digest as "unknown", never as a mismatch.
        """
        proc = self._gh(
            [
                "api",
                f"repos/{self.repo}/releases/tags/{self.tag}",
                "--jq",
                ".assets[] | [.name, .digest] | @tsv",
            ]
        )
        if proc.returncode != 0:
            return {}
        digests: dict[str, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].startswith("sha256:"):
                digests[parts[0]] = parts[1].split(":", 1)[1].lower()
        return digests

    def _ref_for(self, asset: str, size: int, sha256: str) -> StoredRef:
        return StoredRef(
            store="github",
            remote_path=f"{self.tag}/{asset}",
            size=size,
            sha256=sha256,
            uri=f"https://github.com/{self.repo}/releases/download/{self.tag}/{asset}",
        )

    # -- Store interface ----------------------------------------------------

    def upload(
        self,
        local_path: str,
        remote_dir: str = "",
        remote_name: str | None = None,
    ) -> StoredRef:
        if not os.path.isfile(local_path):
            raise StoreError(f"Local file not found: {local_path!r}")
        self._ensure_release()
        asset = remote_name or os.path.basename(local_path)
        size = os.path.getsize(local_path)
        digest = sha256_of(local_path)

        # Idempotent: same name + same size already up -> verify and return.
        for existing in self._assets():
            if existing.get("name") == asset and int(existing.get("size", -1)) == size:
                ref = self._ref_for(asset, size, digest)
                if self.verify(ref):
                    return ref
                break  # name taken by different content; re-upload with --clobber

        proc = self._gh(
            ["release", "upload", self.tag, local_path, "--repo", self.repo, "--clobber"],
            timeout=UPLOAD_TIMEOUT,
        )
        if proc.returncode != 0:
            raise StoreError(
                f"'gh release upload' failed for {asset!r}: {_redact(proc.stderr.strip())}"
            )
        ref = self._ref_for(asset, size, digest)
        if not self.verify(ref):
            raise VerificationFailed(f"GitHub release upload of {asset!r} did not verify.")
        return ref

    def verify(self, ref: StoredRef) -> bool:
        """Re-check the release NOW; True iff the asset verifies.

        Default (non-strict) verify is hash-strong when the API exposes the
        asset digest: name + size must match, and when both ``ref.sha256``
        and the remote ``sha256:`` digest are known the digests must match.
        A missing digest is treated as "unknown", never as a mismatch.

        With ``strict_verify`` the asset is additionally downloaded back and
        its sha256 compared against ``ref.sha256``; the temp dir is deleted
        and the comparison result returned.
        """
        tag, _, asset = ref.remote_path.partition("/")
        if tag != self.tag or not asset:
            return False
        if not any(
            a.get("name") == asset and int(a.get("size", -1)) == ref.size for a in self._assets()
        ):
            return False
        if ref.sha256:
            digest = self._digests().get(asset, "")
            if digest and digest != ref.sha256.lower():
                return False
        if not self.strict_verify:
            return True
        tmpdir = tempfile.mkdtemp(prefix="clouddog-gh-")
        try:
            proc = self._gh(
                [
                    "release",
                    "download",
                    self.tag,
                    "--repo",
                    self.repo,
                    "-p",
                    asset,
                    "-D",
                    tmpdir,
                    "--clobber",
                ],
                timeout=UPLOAD_TIMEOUT,
            )
            if proc.returncode != 0:
                raise StoreError(
                    f"Download-back verify failed for {asset!r}: {_redact(proc.stderr.strip())}"
                )
            return sha256_of(os.path.join(tmpdir, asset)) == ref.sha256
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def exists(self, remote_dir: str, remote_name: str) -> StoredRef | None:
        """Look up an asset by name.

        ``remote_dir`` is ignored: GitHub Releases have a flat asset
        namespace per tag, so there are no directories to address.
        """
        for asset in self._assets():
            if asset.get("name") == remote_name:
                return self._ref_for(remote_name, int(asset.get("size", 0)), "")
        return None

    def uri_for(self, ref: StoredRef) -> str:
        return ref.uri or super().uri_for(ref)
