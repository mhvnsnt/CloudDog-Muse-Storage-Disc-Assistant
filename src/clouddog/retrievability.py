"""Retrievability prover: pull-back actually tested, not just listed.

Owner law (2026-10-17, game-file 100% rule): a game file is clearable ONLY
when it is 100% byte-verified on a store AND retrievability is PROVEN AND
the store was surfaced to the owner. "A listing alone is not proof" — the
release API saying an asset exists proves nothing about whether the bytes
can actually be pulled back. This module performs the real pull-back test:

    prove_retrievable(stored_ref) -> ProofResult

Given a StoredRef (currently: a GitHub release asset locator + expected
sha256 + size), it actually FETCHES the bytes and hash-checks them:

- downloads stream to a scratch file under ~/.clouddog/tmp (never /tmp,
  which is a 512MB tmpfs here; never held in RAM — multi-GB assets stream
  in 1 MiB chunks straight to disk, then hash in 8 MiB chunks);
- retries with exponential backoff on the proxy flakiness this fleet sees
  (connection refused, empty responses, transient 401s, 5xx);
- resumes via HTTP Range requests where the server supports them;
- auth comes from the existing ``gh`` CLI session (``gh auth token``,
  used transiently as an Authorization header — never logged, never
  written to disk, never stored in the repo);
- if full proof is infeasible for an asset (unsupported backend, missing
  checksum, auth failure, repeated transport failure), the result is
  proven=False with the exact reason — fail closed, never faked.

Stdlib only. No intra-package imports: the module duck-types the StoredRef
(``backend`` / ``locator`` / ``size`` / ``sha256`` / ``extra`` attributes,
or a dict with the same keys), so ``cloud_dog.py`` can load it by file path
without disturbing the root-level ``clouddog/`` package shadowing.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "ProofResult",
    "prove_retrievable",
    "prove_registry_entry",
    "SUPPORTED_BACKENDS",
    "DEFAULT_SCRATCH_DIR",
]

#: Backends with a real pull-back implementation. Everything else fails
#: closed (proven=False) with the reason naming the gap.
SUPPORTED_BACKENDS = ("github",)

#: Scratch root for pull-back downloads. Under ~/.clouddog/tmp (the PR
#: #36 convention), never /tmp: the VM's /tmp is a 512MB tmpfs that fills
#: independently of the disk, and game assets are multi-GB.
DEFAULT_SCRATCH_DIR = os.path.join(os.path.expanduser("~/.clouddog"), "tmp")

#: Override the scratch root (e.g. in tests).
SCRATCH_ENV_VAR = "CLOUDDOG_PROOF_SCRATCH"

_CHUNK_BYTES = 1024 * 1024  # 1 MiB streaming writes
_HASH_CHUNK_BYTES = 8 * 1024 * 1024  # 8 MiB hashing reads
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_S = 2.0
_MAX_REDIRECTS = 5
_CONNECT_TIMEOUT_S = 60
_MIN_FREE_BYTES = 64 * 1024 * 1024  # headroom kept on the scratch fs

#: HTTP statuses worth one more attempt (with backoff).
_TRANSIENT_STATUS = frozenset({401, 403, 408, 425, 429, 500, 502, 503, 504})


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProofResult:
    """Outcome of one pull-back proof attempt."""

    proven: bool = False
    bytes_fetched: int = 0
    hash_matched: bool = False
    size_matched: bool = False
    expected_sha256: str = ""
    actual_sha256: str = ""
    expected_size: int = 0
    locator: str = ""
    ts: str = field(default_factory=_utcnow_iso)  # UTC ISO, when proof ran
    attempts: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "proven": self.proven,
            "bytes_fetched": self.bytes_fetched,
            "hash_matched": self.hash_matched,
            "size_matched": self.size_matched,
            "expected_sha256": self.expected_sha256,
            "actual_sha256": self.actual_sha256,
            "expected_size": self.expected_size,
            "locator": self.locator,
            "ts": self.ts,
            "attempts": self.attempts,
            "reason": self.reason,
        }


def _ref_get(ref, name, default=None):
    """Read a StoredRef field from an object or a dict (duck-typed)."""
    if isinstance(ref, dict):
        return ref.get(name, default)
    return getattr(ref, name, default)


def _scratch_root(scratch_dir: str | None) -> str:
    root = scratch_dir or os.environ.get(SCRATCH_ENV_VAR) or DEFAULT_SCRATCH_DIR
    os.makedirs(root, exist_ok=True)
    return root


# ------------------------------------------------------------------ #
# gh session auth
# ------------------------------------------------------------------ #
def _gh_auth_token() -> str | None:
    """Bearer token from the existing ``gh`` CLI session.

    Transient in-memory use only: returned to the caller for an
    Authorization header, never logged, never written anywhere. None when
    the session has no usable token (public repos still work anonymous).
    """
    try:
        proc = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    token = (proc.stdout or "").strip()
    return token or None


# ------------------------------------------------------------------ #
# minimal HTTP layer with manual redirect + Range handling
# ------------------------------------------------------------------ #
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects automatically: the caller re-issues with the
    Range / Authorization headers intact (urllib would drop them)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N803
        return None


def _open(url: str, headers: dict, timeout: int = _CONNECT_TIMEOUT_S):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers=headers)
    return opener.open(req, timeout=timeout)


def _is_transient(exc: BaseException) -> bool:
    """True for the proxy/network flakiness worth retrying."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_STATUS
    # URLError (connection refused, DNS, proxy errors), timeouts, dropped
    # connections mid-stream, empty reads, and our own short-read IOErrors
    # — all transient here.
    return isinstance(
        exc,
        (
            urllib.error.URLError,
            socket.timeout,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
        ),
    )


def _backoff(attempt: int) -> None:
    time.sleep(_BACKOFF_BASE_S * (2**attempt) + random.uniform(0, 0.5))


# ------------------------------------------------------------------ #
# GitHub asset resolution (metadata only; the binary fetch is ranged)
# ------------------------------------------------------------------ #
@dataclass
class _AssetInfo:
    name: str
    api_url: str  # https://api.github.com/repos/.../releases/assets/<id>
    size: int


def _github_headers(token: str | None, accept_octet: bool = False) -> dict:
    headers = {"User-Agent": "clouddog-retrievability-prover/1.0"}
    if accept_octet:
        headers["Accept"] = "application/octet-stream"
    else:
        headers["Accept"] = "application/vnd.github+json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _parse_github_locator(locator: str) -> tuple[str, str, str]:
    """Split ``owner/repo@tag/asset`` into (repo, tag, asset)."""
    head, sep, asset = (locator or "").rpartition("/")
    if not sep or not asset:
        raise ValueError(f"github locator must be 'owner/repo@tag/asset', got {locator!r}")
    repo, at, tag = head.rpartition("@")
    if not at or "/" not in repo or not tag:
        raise ValueError(f"github locator must be 'owner/repo@tag/asset', got {locator!r}")
    return repo, tag, asset


def _resolve_asset_infos(
    repo: str, tag: str, asset_names: list[str], token: str | None
) -> list[_AssetInfo]:
    """Resolve release asset names to their API download URLs.

    Uses the gh-session token (when available) via plain urllib so the
    binary fetch below can do ranged, resumable downloads. Raises on
    transport failure; the caller converts to ProofResult.
    """
    url = f"https://api.github.com/repos/{repo}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    try:
        with _open(url, _github_headers(token)) as resp:
            if resp.status != 200:
                raise urllib.error.HTTPError(
                    url, resp.status, f"release lookup returned {resp.status}", None, None
                )
            release = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError(f"release {repo}@{tag} not found (404)") from exc
        raise
    assets = {a.get("name"): a for a in release.get("assets", []) if a.get("name")}
    infos = []
    for name in asset_names:
        meta = assets.get(name)
        if meta is None or not meta.get("url"):
            raise LookupError(f"asset {name!r} not found on release {repo}@{tag}")
        infos.append(
            _AssetInfo(name=name, api_url=meta["url"], size=int(meta.get("size", 0)))
        )
    return infos


def _download_one(
    info: _AssetInfo,
    dest_path: str,
    token: str | None,
    stats: dict,
    resolve_fresh_url=None,
) -> None:
    """Download one asset to dest_path with resume + retries.

    Raises on permanent failure (after _MAX_ATTEMPTS) or non-transient
    HTTP errors. stats["attempts"] counts every HTTP attempt made.
    resolve_fresh_url, when given, is called to re-resolve the download
    URL after a 403 (expired signed redirect).
    """
    url = info.api_url
    for attempt in range(_MAX_ATTEMPTS):
        have = os.path.getsize(dest_path) if os.path.exists(dest_path) else 0
        headers = _github_headers(token, accept_octet=True)
        if have:
            headers["Range"] = f"bytes={have}-"
        stats["attempts"] += 1
        try:
            final_url, status, data_iter, content_len = _fetch_stream(
                url, headers, token
            )
            url = final_url  # remember the post-redirect URL for 403 refresh
            if status == 416:  # Range unsatisfiable
                if info.size and have == info.size:
                    return  # already complete
                # Stale partial: restart from zero.
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                continue
            mode = "ab" if (status == 206 and have) else "wb"
            if status == 200 and have:
                have = 0  # server ignored Range; restart cleanly
            fetched = have
            with open(dest_path, mode) as fh:
                for chunk in data_iter:
                    fh.write(chunk)
                    fetched += len(chunk)
            if info.size and fetched != info.size:
                raise IOError(
                    f"short read for {info.name}: got {fetched} bytes, "
                    f"release metadata says {info.size}"
                )
            if fetched == 0 and info.size:
                raise IOError(f"empty response for {info.name} (expected {info.size} bytes)")
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 403 and resolve_fresh_url is not None:
                # Signed redirect URLs expire: re-resolve once per 403,
                # keep the partial file, then retry with Range intact.
                try:
                    url = resolve_fresh_url()
                except Exception:
                    pass
            if exc.code in _TRANSIENT_STATUS and attempt < _MAX_ATTEMPTS - 1:
                _backoff(attempt)
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - transient net errors
            if _is_transient(exc) and attempt < _MAX_ATTEMPTS - 1:
                _backoff(attempt)
                continue
            raise
    raise IOError(f"exhausted {_MAX_ATTEMPTS} attempts downloading {info.name}")


def _fetch_stream(url: str, headers: dict, token: str | None):
    """GET url, following up to _MAX_REDIRECTS redirects manually.

    Returns (final_url, status, chunk_iterator, content_length|None).
    The caller owns closing: the iterator exhausts the response.
    """
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            resp = _open(current, headers)
        except urllib.error.HTTPError as exc:
            # _NoRedirect makes urllib raise on 3xx: the Location header
            # on the exception IS the redirect target.
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location") if exc.headers else None
                if not location:
                    raise IOError(
                        f"redirect ({exc.code}) without Location from {current}"
                    ) from exc
                # Re-issue against the target with Range/Auth intact.
                current = urllib.parse.urljoin(current, location)
                continue
            raise
        try:
            status = resp.status
            if status not in (200, 206):
                body = resp.read(512)
                resp.close()
                raise urllib.error.HTTPError(
                    current, status, f"unexpected status {status}: {body[:200]!r}",
                    None, None,
                )
            content_len = resp.getheader("Content-Length")
            length = int(content_len) if content_len and content_len.isdigit() else None

            def _chunks(r=resp):
                try:
                    while True:
                        chunk = r.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    r.close()

            return current, status, _chunks(), length
        except Exception:
            # _chunks closes resp on exhaustion; close now on early failure.
            try:
                resp.close()
            except Exception:
                pass
            raise
    raise IOError(f"too many redirects fetching {url}")


def _sha256_stream(paths: list[str]) -> str:
    """sha256 over concatenated files, 8 MiB at a time (never in RAM)."""
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ #
# public API
# ------------------------------------------------------------------ #
def prove_retrievable(stored_ref, scratch_dir: str | None = None) -> ProofResult:
    """Pull-back proof for one StoredRef: fetch the bytes, hash-check them.

    ``stored_ref`` is a StoredRef (``src/clouddog/stores/base.py``) or any
    object/dict with ``backend`` / ``locator`` / ``size`` / ``sha256`` /
    ``extra``. Only the ``github`` backend is implemented; anything else
    returns proven=False (fail closed) with the reason.

    Returns a ProofResult. Never raises on proof failure — infeasible
    proofs are reported via ``proven=False`` + ``reason``.
    """
    backend = (_ref_get(stored_ref, "backend", "") or "").lower()
    locator = _ref_get(stored_ref, "locator", "") or ""
    expected_size = _ref_get(stored_ref, "size", 0) or 0
    expected_sha256 = (_ref_get(stored_ref, "sha256", "") or "").lower()
    extra = _ref_get(stored_ref, "extra", None) or {}

    def _refuse(reason: str) -> ProofResult:
        return ProofResult(
            proven=False,
            expected_sha256=expected_sha256,
            expected_size=expected_size if isinstance(expected_size, int) else 0,
            locator=locator,
            reason=reason,
        )

    if backend not in SUPPORTED_BACKENDS:
        return _refuse(
            f"backend {backend!r}: retrievability proof is implemented for "
            f"{', '.join(SUPPORTED_BACKENDS)} only; refusing (fail closed)"
        )
    if not locator:
        return _refuse("empty locator: nothing to pull back")
    if not expected_sha256 or len(expected_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in expected_sha256
    ):
        return _refuse(
            "no usable expected sha256 on the StoredRef: cannot prove "
            "byte-identity without it; refusing (fail closed)"
        )
    if not isinstance(expected_size, int) or expected_size <= 0:
        return _refuse(
            "no usable expected size on the StoredRef: cannot prove "
            "completeness without it; refusing (fail closed)"
        )
    try:
        repo, tag, asset = _parse_github_locator(locator)
    except ValueError as exc:
        return _refuse(str(exc))

    asset_names = extra.get("asset_names") if isinstance(extra, dict) else None
    if not asset_names:
        asset_names = [asset]
    asset_names = sorted(set(asset_names))

    stats = {"attempts": 0}
    workdir = None
    try:
        root = _scratch_root(scratch_dir)
        # Disk-space gate: fail closed rather than die mid-download.
        # Headroom scales with the asset (filesystem overhead), capped at
        # _MIN_FREE_BYTES so small proofs aren't blocked by it.
        try:
            free = shutil.disk_usage(root).free
        except OSError:
            free = 0
        total_expected = expected_size + min(_MIN_FREE_BYTES, expected_size)
        if free < total_expected:
            return _refuse(
                f"insufficient scratch space: need ~{expected_size} bytes for "
                f"the pull-back on {root} ({free} free); refusing (fail closed)"
            )
        workdir = tempfile.mkdtemp(prefix="proof-", dir=root)
        token = _gh_auth_token()

        def _fresh_url(name: str) -> str:
            infos = _resolve_asset_infos(repo, tag, [name], token)
            return infos[0].api_url

        infos = _resolve_asset_infos(repo, tag, asset_names, token)
        for info in infos:
            if info.size and info.size != expected_size and len(infos) == 1:
                return _refuse(
                    f"release metadata size {info.size} != expected "
                    f"{expected_size} for {info.name}: the store changed "
                    "under us; refusing (fail closed)"
                )
            dest = os.path.join(workdir, f"part-{uuid.uuid4().hex}")
            _download_one(
                info, dest, token, stats,
                resolve_fresh_url=(lambda n=info.name: _fresh_url(n)),
            )
        parts = sorted(
            os.path.join(workdir, p) for p in os.listdir(workdir)
        )
        if not parts:
            return _refuse("download produced no bytes; refusing (fail closed)")
        bytes_fetched = sum(os.path.getsize(p) for p in parts)
        size_matched = bytes_fetched == expected_size
        actual_sha256 = _sha256_stream(parts)
        hash_matched = actual_sha256 == expected_sha256
        if not size_matched:
            return ProofResult(
                proven=False, bytes_fetched=bytes_fetched,
                hash_matched=hash_matched, size_matched=False,
                expected_sha256=expected_sha256, actual_sha256=actual_sha256,
                expected_size=expected_size, locator=locator,
                attempts=stats["attempts"],
                reason=f"size mismatch after pull-back: fetched {bytes_fetched} "
                       f"bytes, expected {expected_size}",
            )
        if not hash_matched:
            return ProofResult(
                proven=False, bytes_fetched=bytes_fetched,
                hash_matched=False, size_matched=True,
                expected_sha256=expected_sha256, actual_sha256=actual_sha256,
                expected_size=expected_size, locator=locator,
                attempts=stats["attempts"],
                reason="sha256 mismatch after pull-back: the bytes on the "
                       "store are NOT byte-identical to the recorded digest",
            )
        return ProofResult(
            proven=True, bytes_fetched=bytes_fetched,
            hash_matched=True, size_matched=True,
            expected_sha256=expected_sha256, actual_sha256=actual_sha256,
            expected_size=expected_size, locator=locator,
            attempts=stats["attempts"],
            reason="pull-back fetched and hash-verified",
        )
    except (LookupError, ValueError, IOError, OSError,
            urllib.error.URLError, http.client.HTTPException,
            socket.timeout) as exc:
        return ProofResult(
            proven=False,
            expected_sha256=expected_sha256,
            expected_size=expected_size if isinstance(expected_size, int) else 0,
            locator=locator,
            attempts=stats["attempts"],
            reason=f"pull-back failed: {str(exc)[:300]}",
        )
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def prove_registry_entry(entry: dict, scratch_dir: str | None = None) -> ProofResult:
    """Build a StoredRef from a cloud_dog registry entry and prove it.

    Registry entries carry ``store`` / ``repo`` / ``release`` / ``asset`` /
    ``sha256`` / ``size``. Non-github stores and entries missing the
    checksum/size needed for byte-identity fail closed with the reason.
    """
    entry = entry or {}
    store = (entry.get("store") or "").lower()
    locator = ""
    if store == "github":
        repo = entry.get("repo") or ""
        release = entry.get("release") or ""
        asset = entry.get("asset") or ""
        if repo and release and asset:
            locator = f"{repo}@{release}/{asset}"
    ref = {
        "backend": store,
        "locator": locator,
        "size": entry.get("size") or 0,
        "sha256": entry.get("sha256") or "",
        "extra": {"asset_names": [entry.get("asset")] if entry.get("asset") else []},
    }
    return prove_retrievable(ref, scratch_dir=scratch_dir)
