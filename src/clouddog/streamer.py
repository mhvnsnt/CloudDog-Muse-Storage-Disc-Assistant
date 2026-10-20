"""Watch-dir streamer: stream downloads to the store WHILE they land.

Owner order (2026-10-19): lanes write gigabytes to local disk first and
think about storage later — disk sits near full while downloads sit whole.
Stream mode is the new default for download-shaped work: a file that lands
in a designated watch dir is pushed to the store (GitHub release) in
~100 MB chunks as soon as it settles, each chunk is byte-verified against
the LIVE store, and the local bytes are deleted the moment their chunk
verifies (DELETE-AS-YOU-GO) — disk falls DURING the lane's work, not after.

Iron rules (~/AGENTS.md + this repo's AGENTS.md):
  * NEVER-KILL-IN-PROGRESS / NO-SWEEP-INFLIGHT: an active partial is never
    touched — in-flight suffixes (.part/.aria2/.tmp/.downloading/...),
    .DO_NOT_DELETE markers, and anything still being written (size/mtime
    not settled) are skipped. Settle = size+mtime unchanged for
    ``settle_secs`` (default 300, the policy engine's sacred window).
  * ONE-STORE RULE: local bytes clear only after the store copy verifies
    hash-identical against the LIVE store (backend.store() re-downloads
    and sha256-compares; a manifest entry alone is a claim, not proof).
  * The checkpoint manifest (.clouddog-stream-manifest.json in the watch
    dir) is SACRED — never deleted, never reaped; it is the resume record
    and the sole source for the final published manifest.
  * SCOOBY LESSON: the final manifest asset for a multi-chunk file is
    built FROM THE CHECKPOINT MANIFEST, never by re-walking a source dir
    the streamer may have pruned — a re-walk reads an empty dir and the
    upload fails at the last step.
  * GAME FILES (100% rule, owner 2026-10-17): streamed and verified like
    everything else, but the local copy clears ONLY at the strict bar —
    100% byte-verified store + retrievability PROVEN (pull-back actually
    tested) + owner surfaced. Without all three, the file stays local.
  * Dry-run by default; --apply performs real uploads/deletes.

Chunk size default is 100 MB: the size the threshold research found
actually survives the egress proxy (485 MB+ parts fail; 100 MB works).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from clouddog import policy
from clouddog.errors import CloudDogError

#: Local checkpoint manifest (inside the watch dir). SACRED: the resume
#: record and the only source for the final published manifest asset.
#: Never deleted by the streamer or the reaper.
MANIFEST_NAME = ".clouddog-stream-manifest.json"

#: 100 MB — proxy-survivable chunk size (threshold research, 2026-10-17).
DEFAULT_CHUNK_BYTES = 100 * 1024 * 1024

#: Settle window before a landed file counts as complete (seconds).
#: Matches the policy engine's five-minute sacred window by default.
DEFAULT_SETTLE_SECS = 300

#: Default store when a lane does not name one (created by PR #37 lane).
DEFAULT_REPO = "mhvnsnt/clouddog-storage"

_MANIFEST_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str) -> str:
    """Streaming sha256 of a file (8 MiB reads; safe for multi-GB)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------------ manifest


@dataclass
class ChunkRecord:
    """One uploaded chunk of a streamed file."""

    index: int
    asset: str
    size: int
    sha256: str = ""
    locator: str = ""
    verified: bool = False

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "asset": self.asset,
            "size": self.size,
            "sha256": self.sha256,
            "locator": self.locator,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChunkRecord:
        return cls(
            index=int(d["index"]),
            asset=str(d["asset"]),
            size=int(d["size"]),
            sha256=str(d.get("sha256", "")),
            locator=str(d.get("locator", "")),
            verified=bool(d.get("verified", False)),
        )


@dataclass
class FileRecord:
    """Checkpoint state for one file being streamed."""

    name: str  # basename inside the watch dir
    path: str  # absolute local path
    size: int = 0
    sha256: str = ""  # whole-file sha256
    strict: bool = False  # game-file strict bar applies
    status: str = "pending"  # pending|streaming|stored|cleared|blocked|failed
    chunks: list[ChunkRecord] = field(default_factory=list)
    manifest_asset: str = ""  # locator of the published manifest asset
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
            "strict": self.strict,
            "status": self.status,
            "chunks": [c.to_dict() for c in self.chunks],
            "manifest_asset": self.manifest_asset,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> FileRecord:
        return cls(
            name=str(d["name"]),
            path=str(d["path"]),
            size=int(d.get("size", 0)),
            sha256=str(d.get("sha256", "")),
            strict=bool(d.get("strict", False)),
            status=str(d.get("status", "pending")),
            chunks=[ChunkRecord.from_dict(c) for c in d.get("chunks", [])],
            manifest_asset=str(d.get("manifest_asset", "")),
            error=str(d.get("error", "")),
        )

    def chunk_by_index(self, index: int) -> ChunkRecord | None:
        for c in self.chunks:
            if c.index == index:
                return c
        return None


@dataclass
class StreamManifest:
    """Checkpoint manifest for one watch dir + store target.

    Written after EVERY chunk (atomic tmp+replace+fsync) so a crash or a
    runtime restart can resume exactly where the stream left off. This
    file is SACRED: it is never deleted, and the final published manifest
    asset is built from it (never from re-walking the watch dir).
    """

    watch_dir: str
    repo: str
    tag: str
    chunk_bytes: int = DEFAULT_CHUNK_BYTES
    version: int = _MANIFEST_VERSION
    created_utc: str = ""
    updated_utc: str = ""
    # name -> [size, mtime, first_stable_seen_ts] for settle tracking
    seen: dict[str, list] = field(default_factory=dict)
    files: dict[str, FileRecord] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "watch_dir": self.watch_dir,
            "repo": self.repo,
            "tag": self.tag,
            "chunk_bytes": self.chunk_bytes,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "seen": self.seen,
            "files": {k: v.to_dict() for k, v in self.files.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> StreamManifest:
        return cls(
            watch_dir=str(d["watch_dir"]),
            repo=str(d["repo"]),
            tag=str(d["tag"]),
            chunk_bytes=int(d.get("chunk_bytes", DEFAULT_CHUNK_BYTES)),
            version=int(d.get("version", _MANIFEST_VERSION)),
            created_utc=str(d.get("created_utc", "")),
            updated_utc=str(d.get("updated_utc", "")),
            seen=dict(d.get("seen", {})),
            files={k: FileRecord.from_dict(v) for k, v in d.get("files", {}).items()},
        )


def manifest_path(watch_dir: str) -> str:
    return os.path.join(watch_dir, MANIFEST_NAME)


def load_manifest(
    watch_dir: str,
    repo: str,
    tag: str,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
) -> StreamManifest:
    """Load the checkpoint manifest, or start a fresh one.

    A manifest for a DIFFERENT store target is never silently reused —
    that would mix two streams' accounting. Raises CloudDogError.
    """
    path = manifest_path(watch_dir)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            m = StreamManifest.from_dict(data)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise CloudDogError(f"cannot read stream manifest {path}: {exc}") from exc
        if (m.repo, m.tag, m.chunk_bytes) != (repo, tag, chunk_bytes):
            raise CloudDogError(
                f"stream manifest {path} belongs to {m.repo}@{m.tag} "
                f"(chunk {m.chunk_bytes}B), not {repo}@{tag} "
                f"(chunk {chunk_bytes}B); refusing to mix streams"
            )
        return m
    return StreamManifest(
        watch_dir=os.path.abspath(watch_dir),
        repo=repo,
        tag=tag,
        chunk_bytes=chunk_bytes,
        created_utc=_utcnow(),
        updated_utc=_utcnow(),
    )


def save_manifest(manifest: StreamManifest) -> None:
    """Atomic checkpoint write: tmp file + fsync + os.replace."""
    manifest.updated_utc = _utcnow()
    path = manifest_path(manifest.watch_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest.to_dict(), fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


# ------------------------------------------------------------------ planning


def chunk_plan(size: int, chunk_bytes: int) -> list[tuple[int, int, int]]:
    """(index, offset, length) per chunk. Empty for size <= 0."""
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    if size <= 0:
        return []
    n = (size + chunk_bytes - 1) // chunk_bytes
    return [(i, i * chunk_bytes, min(chunk_bytes, size - i * chunk_bytes)) for i in range(n)]


def chunk_asset_name(basename: str, index: int, total: int) -> str:
    """Release asset name for one chunk. Single-chunk files keep the
    plain basename; multi-chunk files get a stable, sortable suffix."""
    if total <= 1:
        return basename
    return f"{basename}.chunk-{index:04d}"


def manifest_asset_name(basename: str) -> str:
    """Release asset name for the published (final) manifest."""
    return f"{basename}.stream-manifest.json"


# ------------------------------------------------------------------ gates


def stream_gate(path: str, watch_dir: str) -> tuple[bool, bool, str]:
    """Policy gate for one watch-dir file.

    Returns (allowed, strict, reason). SACRED and KEEP_LOCAL are blocked;
    STORE_AND_CLEAR_STRICT (game files) is allowed to stream but flagged
    strict — the local copy may clear only at the strict bar.
    """
    dec = policy.evaluate(path, scan_root=watch_dir)
    if dec.action == policy.Action.SACRED:
        return False, False, dec.reason
    if dec.action == policy.Action.KEEP_LOCAL:
        return False, False, dec.reason
    if dec.action == policy.Action.STORE_AND_CLEAR_STRICT:
        return True, True, dec.reason
    return True, False, dec.reason


def scan_watch(
    watch_dir: str,
    manifest: StreamManifest,
    settle_secs: int = DEFAULT_SETTLE_SECS,
    now: float | None = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """One settle scan of the watch dir.

    Returns (ready_paths, skipped[(name, reason)]). A file is ready only
    when its size+mtime have been unchanged for >= settle_secs across
    scans (tracked in the manifest's ``seen`` table, so the check
    survives restarts). Active partials are NEVER ready: in-flight
    suffixes, .DO_NOT_DELETE markers, sacred/keep-local policy verdicts,
    zero-size files, symlinks, and directories are all skipped.

    Side effect: updates manifest.seen (caller saves the manifest).
    """
    now = time.time() if now is None else now
    ready: list[str] = []
    skipped: list[tuple[str, str]] = []
    try:
        children = sorted(os.listdir(watch_dir))
    except OSError as exc:
        return [], [(watch_dir, f"cannot list watch dir: {exc}")]

    for name in children:
        if name == MANIFEST_NAME or name.startswith(".clouddog-stream"):
            continue  # the checkpoint manifest itself: SACRED, invisible
        full = os.path.join(watch_dir, name)
        if os.path.islink(full):
            skipped.append((name, "symlink (never followed)"))
            continue
        if os.path.isdir(full):
            skipped.append((name, "directory (stream mode is file-only)"))
            continue
        if not os.path.isfile(full):
            skipped.append((name, "not a regular file"))
            continue
        lower = name.lower()
        if lower.endswith(tuple(policy.IN_FLIGHT_SUFFIXES)):
            skipped.append((name, "in-flight suffix (NEVER-KILL-IN-PROGRESS)"))
            continue
        try:
            st = os.stat(full)
        except OSError as exc:
            skipped.append((name, f"unreadable: {exc}"))
            continue
        if st.st_size == 0:
            skipped.append((name, "empty file (writer has not started)"))
            continue
        allowed, _strict, reason = stream_gate(full, watch_dir)
        if not allowed:
            skipped.append((name, reason))
            continue
        prev = manifest.seen.get(name)
        if prev is None or int(prev[0]) != st.st_size or float(prev[1]) != st.st_mtime:
            manifest.seen[name] = [st.st_size, st.st_mtime, now]
            skipped.append((name, "settling (size/mtime just changed)"))
            continue
        if now - float(prev[2]) < settle_secs:
            left = int(settle_secs - (now - float(prev[2])))
            skipped.append((name, f"settling ({left}s left)"))
            continue
        ready.append(full)
    return ready, skipped
