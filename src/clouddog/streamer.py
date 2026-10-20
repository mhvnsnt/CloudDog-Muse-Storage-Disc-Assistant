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
from clouddog.stores.base import CHUNK_BYTES

#: Local checkpoint manifest (inside the watch dir). SACRED: the resume
#: record and the only source for the final published manifest asset.
#: Never deleted by the streamer or the reaper.
MANIFEST_NAME = ".clouddog-stream-manifest.json"

#: THE single chunk standard (owner decision 2026-10-19: 100MB for
#: store() and mirror everywhere) — an alias of
#: ``clouddog.stores.base.CHUNK_BYTES``. The value matches the size the
#: threshold research found survives the egress proxy (485 MB+ parts
#: fail; 100 MB works). Change it in stores/base.py, never here.
DEFAULT_CHUNK_BYTES = CHUNK_BYTES

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


# ------------------------------------------------------------------ streaming


@dataclass
class StreamReport:
    """Outcome of one stream pass."""

    files_streamed: int = 0
    files_cleared: int = 0
    files_blocked: int = 0
    chunks_uploaded: int = 0
    bytes_streamed: int = 0
    freed_bytes: int = 0
    skipped: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # Scorecard (training layer, 2026-10-20): correctness, not bytes.
    correct_clears: int = 0
    correct_blocks: int = 0
    correct_kept: int = 0
    near_misses: int = 0
    violations: int = 0
    error_actions: int = 0


def _scratch_dir() -> str:
    """Scratch space for chunk extraction. NEVER /tmp (512MB tmpfs)."""
    import tempfile

    from clouddog import trust as _trust

    base = os.path.join(_trust.state_dir(), "tmp")
    os.makedirs(base, exist_ok=True)
    return tempfile.mkdtemp(prefix="clouddog-stream-", dir=base)


def _extract_chunk(src: str, offset: int, length: int, dest: str) -> str:
    """Copy src[offset:offset+length] to dest (streaming). Returns sha256
    of the extracted bytes."""
    digest = hashlib.sha256()
    remaining = length
    with open(src, "rb") as fin, open(dest, "wb") as fout:
        fin.seek(offset)
        while remaining > 0:
            data = fin.read(min(8 * 1024 * 1024, remaining))
            if not data:
                raise CloudDogError(f"short read extracting chunk at offset {offset} from {src}")
            fout.write(data)
            digest.update(data)
            remaining -= len(data)
    return digest.hexdigest()


def build_final_manifest(rec: FileRecord, repo: str, tag: str) -> dict:
    """The published manifest payload for one streamed file.

    SCOOBY LESSON: built FROM THE CHECKPOINT RECORD — never by re-walking
    the watch dir (the streamer prunes local bytes as it goes; a re-walk
    would see an empty dir and lose the file's accounting at the last
    step). The chunks listed here are already byte-verified on the store.
    """
    chunks = [c.to_dict() for c in sorted(rec.chunks, key=lambda c: c.index)]
    assets = [c["asset"] for c in chunks]
    return {
        "name": rec.name,
        "size": rec.size,
        "sha256": rec.sha256,
        "repo": repo,
        "tag": tag,
        "chunks": chunks,
        "created_utc": _utcnow(),
        "tool": "clouddog-stream/1",
        "reassemble": (
            f"gh release download {tag} -R {repo} "
            f"-p '{rec.name}.chunk-*' -p '{rec.name}' ; "
            f"cat {' '.join(assets)} > {rec.name} ; "
            f"sha256sum {rec.name}  # expect {rec.sha256}"
        ),
    }


class Streamer:
    """Stream settled watch-dir files to a GitHub release, delete-as-you-go.

    Per file: hash -> split into chunks (scratch) -> for each chunk,
    backend.store() (which uploads AND byte-verifies against the live
    store by re-downloading) -> checkpoint the manifest -> delete the
    scratch chunk -> publish the final manifest asset FROM the checkpoint
    -> delete the local source (only after every chunk verified; game
    files additionally need the strict bar). Peak extra disk: one chunk.
    """

    def __init__(
        self,
        watch_dir: str,
        backend,
        repo: str = DEFAULT_REPO,
        tag: str | None = None,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        settle_secs: int = DEFAULT_SETTLE_SECS,
        apply: bool = False,
        journal=None,
        retrievability_proven: bool = False,
        owner_surfaced: bool = False,
        prover=None,
        claim_check=None,
        trust_path: str | None = None,
        scorecard_journal=None,
    ) -> None:
        self.watch_dir = os.path.abspath(watch_dir)
        self.backend = backend
        self.repo = repo
        self.tag = tag or f"stream-{datetime.now(timezone.utc):%Y%m%d}"
        self.remote_ref = f"{repo}@{self.tag}"
        self.chunk_bytes = int(chunk_bytes)
        self.settle_secs = int(settle_secs)
        self.apply = bool(apply)
        self.journal = journal  # callable(kind, path, size, locator, note)
        self.retrievability_proven = bool(retrievability_proven)
        self.owner_surfaced = bool(owner_surfaced)
        self._prover = prover  # injectable for tests; default = real prover
        # Delete gate (2026-10-20 incident fix): the final unlink passes
        # clouddog.delete_gate; its claims leg consults the work-claims
        # store. Default = the production checker (fail closed when the
        # store is unreadable); injectable for tests.
        if claim_check is None:
            from clouddog.claims import make_claim_checker

            claim_check = make_claim_checker()
        self._claim_check = claim_check
        # Training layer: scorecard journal (AuditJournal-like or None)
        # + the trust file a NEAR_MISS / VIOLATION grade revokes.
        self.trust_path = trust_path
        self._scorecard_journal = scorecard_journal

    # ------------------------------------------------------------ internals

    def _journal(self, kind: str, path: str, size: int, locator: str, note: str = "") -> None:
        if self.journal is None:
            return
        try:
            self.journal(kind, path, size, locator, note)
        except Exception:  # noqa: BLE001, S110
            pass  # journaling must never break a stream

    def _grade(self, report: StreamReport, grade: str, path: str, size: int,
               locator: str, note: str, blocks=(), cleared: bool = False) -> None:
        """Scorecard grade for one streamer action (training layer).

        Counted on the report and journaled when a scorecard journal is
        wired; NEAR_MISS / VIOLATION revokes trust via record_grade.
        """
        from clouddog import scorecard as _scorecard

        if grade == _scorecard.CORRECT:
            if cleared:
                report.correct_clears += 1
            else:
                report.correct_kept += 1
        elif grade == _scorecard.BLOCKED:
            report.correct_blocks += 1
        elif grade == _scorecard.NEAR_MISS:
            report.near_misses += 1
        elif grade == _scorecard.VIOLATION:
            report.violations += 1
        elif grade == _scorecard.ERROR:
            report.error_actions += 1
        try:
            _scorecard.record_grade(
                self._scorecard_journal, grade, path=path, source="streamer",
                size=size, locator=locator, note=note, blocks=blocks,
                trust_path=self.trust_path,
                actor="streamer",
                invocation_id=getattr(self, "_invocation_id", ""),
            )
        except Exception:  # noqa: BLE001 - grading never breaks a stream
            pass

    def _prove_chunks(self, rec: FileRecord) -> tuple[bool, str]:
        """Pull-back proof per chunk: each chunk's StoredRef is fetched
        from the live store and hash-checked (PR #37 prover). Chunks are
        contiguous ranges of the original, so per-chunk byte-identity +
        the recorded whole-file sha256 proves reassembly."""
        from clouddog.stores.base import StoredRef

        prover = self._prover
        if prover is None:
            from clouddog.retrievability import prove_retrievable

            prover = prove_retrievable
        total = 0
        for c in sorted(rec.chunks, key=lambda c: c.index):
            ref = StoredRef(backend="github", locator=c.locator, size=c.size, sha256=c.sha256)
            result = prover(ref)
            if not getattr(result, "proven", False):
                return False, f"chunk {c.index} pull-back failed: {getattr(result, 'reason', '?')}"
            total += c.size
        if total != rec.size:
            return False, f"chunk sizes sum to {total}, file is {rec.size}"
        return True, "all chunks pull-back proven"

    def _strict_bar_ok(self, rec: FileRecord, report: StreamReport) -> bool:
        """Game-file 100% rule (owner 2026-10-17): 100% byte-verified
        store + retrievability PROVEN + owner surfaced. All three or the
        local copy stays."""
        if not self.retrievability_proven:
            rec.error = "strict bar: --retrievability-proven not set; kept local"
            return False
        if not self.owner_surfaced:
            rec.error = "strict bar: --owner-surfaced not set; kept local"
            return False
        ok, why = self._prove_chunks(rec)
        self._journal(
            "stream_retrievability_proof",
            rec.path,
            rec.size,
            self.remote_ref,
            f"proven={ok} {why}",
        )
        if not ok:
            rec.error = f"strict bar: retrievability not proven ({why}); kept local"
            return False
        return True

    def _publish_manifest(self, manifest: StreamManifest, rec: FileRecord) -> None:
        """Upload the final manifest asset, built from the checkpoint
        record (never a re-walk of the watch dir)."""
        payload = build_final_manifest(rec, self.repo, self.tag)
        scratch = _scratch_dir()
        try:
            tmp = os.path.join(scratch, manifest_asset_name(rec.name))
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            stored = self.backend.store(tmp, self.remote_ref, dry_run=not self.apply)
            rec.manifest_asset = stored.locator
        finally:
            import shutil

            shutil.rmtree(scratch, ignore_errors=True)

    def _stream_file(self, manifest: StreamManifest, path: str, report: StreamReport) -> None:
        name = os.path.basename(path)
        allowed, strict, _reason = stream_gate(path, self.watch_dir)
        if not allowed:
            report.skipped.append((name, "gate closed after scan"))
            return
        try:
            size = os.path.getsize(path)
            whole_sha = sha256_file(path)
        except OSError as exc:
            report.errors.append(f"{name}: cannot stat/hash: {exc}")
            return

        rec = manifest.files.get(name)
        if rec is not None and (rec.size, rec.sha256) != (size, whole_sha):
            rec = None  # new content under a known name: fresh record
        if rec is None:
            rec = FileRecord(name=name, path=path, strict=strict)
            if self.apply:
                manifest.files[name] = rec
        rec.size, rec.sha256, rec.strict = size, whole_sha, strict

        plan = chunk_plan(size, self.chunk_bytes)
        if not plan:
            report.skipped.append((name, "empty file"))
            return
        total = len(plan)
        rec.status = "streaming"
        if self.apply:
            save_manifest(manifest)

        scratch = _scratch_dir() if total > 1 else None
        resumed_chunks = 0  # verified in a PREVIOUS run (stale proof)
        try:
            for index, offset, length in plan:
                asset = chunk_asset_name(name, index, total)
                done = rec.chunk_by_index(index)
                if done is not None and done.verified and done.asset == asset:
                    resumed_chunks += 1
                    continue  # resume: already on the store, byte-verified
                if total == 1:
                    chunk_path = path
                else:
                    assert scratch is not None
                    chunk_path = os.path.join(scratch, asset)
                    _extract_chunk(path, offset, length, chunk_path)
                try:
                    stored = self.backend.store(chunk_path, self.remote_ref, dry_run=not self.apply)
                except CloudDogError as exc:
                    rec.status = "failed"
                    rec.error = f"chunk {index} store failed: {exc}"
                    if self.apply:
                        save_manifest(manifest)
                    report.errors.append(f"{name}: {rec.error}")
                    self._grade(report, "ERROR", path, length, self.remote_ref,
                                f"stream store failed, file kept: {exc}")
                    return
                finally:
                    if total > 1 and os.path.exists(chunk_path):
                        os.remove(chunk_path)  # scratch chunk: store has it now
                crec = ChunkRecord(
                    index=index,
                    asset=asset,
                    size=length,
                    sha256=stored.sha256,
                    locator=stored.locator,
                    verified=True,
                )
                rec.chunks = [c for c in rec.chunks if c.index != index] + [crec]
                rec.chunks.sort(key=lambda c: c.index)
                report.chunks_uploaded += 1
                report.bytes_streamed += length
                if self.apply:
                    save_manifest(manifest)  # checkpoint after EVERY chunk
                self._journal(
                    "streamed_chunk",
                    path,
                    length,
                    stored.locator,
                    f"chunk {index + 1}/{total} of {name} verified on store",
                )
        finally:
            if scratch is not None:
                import shutil

                shutil.rmtree(scratch, ignore_errors=True)

        if not rec.manifest_asset:
            try:
                self._publish_manifest(manifest, rec)
            except CloudDogError as exc:
                rec.status = "failed"
                rec.error = f"final manifest publish failed: {exc}"
                if self.apply:
                    save_manifest(manifest)
                report.errors.append(f"{name}: {rec.error}")
                return
            if self.apply:
                save_manifest(manifest)
        rec.status = "stored"
        report.files_streamed += 1
        self._journal(
            "streamed",
            path,
            rec.size,
            self.remote_ref,
            f"{name}: {total} chunk(s) verified; manifest {rec.manifest_asset}",
        )

        if not self.apply:
            self._grade(report, "CORRECT", path, rec.size, self.remote_ref,
                        "stored and kept local (dry-run stream; nothing deleted)")
            return  # dry-run: uploaded nothing, deleted nothing

        if rec.strict and not self._strict_bar_ok(rec, report):
            rec.status = "blocked"
            save_manifest(manifest)
            report.files_blocked += 1
            self._journal("skipped", path, rec.size, self.remote_ref, rec.error)
            self._grade(report, "BLOCKED", path, rec.size, self.remote_ref,
                        f"strict bar kept local: {rec.error}")
            return

        # TOCTOU re-check: the source must be exactly what we hashed.
        try:
            if os.path.getsize(path) != rec.size or sha256_file(path) != rec.sha256:
                rec.error = "source changed since hashing; kept local"
                rec.status = "blocked"
                save_manifest(manifest)
                report.files_blocked += 1
                self._grade(report, "BLOCKED", path, rec.size, self.remote_ref,
                            "kept local: source changed since hashing (TOCTOU check)")
                return
        except OSError as exc:
            report.errors.append(f"{name}: re-stat before delete failed: {exc}")
            return
        # DELETE GATE (2026-10-20 incident fix): the same hard invariant
        # as the daemon/reaper. Registry leg = the sacred checkpoint
        # manifest record (sha256+size of the stored file, final manifest
        # asset published); verify leg = every chunk byte-verified
        # against the live store during THIS run; claims leg = the work
        # claims store (a lane may have claimed the file after it
        # settled); policy leg = stream_gate re-run at delete time.
        from clouddog import policy as _policy
        from clouddog.delete_gate import check_delete_gate

        try:
            _policy_action = _policy.evaluate(path).action.value
        except Exception:  # noqa: BLE001 - no verdict = gate blocks (fail closed)
            _policy_action = ""
        verdict = check_delete_gate(
            path,
            registry_entry={"sha256": rec.sha256, "size": rec.size,
                            "status": "registered"},
            local_sha256=rec.sha256,
            local_size=rec.size,
            live_verify_ok=(
                len(rec.chunks) == total and all(c.verified for c in rec.chunks)
            ),
            claim_check=self._claim_check,
            policy_action=_policy_action,
            # The streamer's charter is delete-as-you-go: STORE (mirror)
            # files clear here too once every leg holds — stream_gate
            # already refused SACRED/KEEP_LOCAL before any upload.
            deletable_actions=frozenset({"store", "store_and_clear", "store_and_clear_strict"}),
        )
        if not verdict.allowed:
            rec.error = f"delete gate blocked: {verdict.summary}"
            rec.status = "blocked"
            save_manifest(manifest)
            report.files_blocked += 1
            self._journal("delete_blocked", path, rec.size, self.remote_ref,
                          verdict.summary)
            from clouddog import scorecard as _scorecard

            self._grade(report, _scorecard.classify_gate_verdict(verdict),
                        path, rec.size, self.remote_ref, verdict.summary,
                        blocks=tuple(verdict.blocks))
            return
        try:
            os.remove(path)
        except OSError as exc:
            report.errors.append(f"{name}: delete after verified store failed: {exc}")
            return
        # Grade the TRACE, not just the outcome: a clear that rode on
        # chunks verified in a PREVIOUS run (resumed checkpoint records)
        # is a NEAR_MISS — the proof was stale, even though the bytes
        # survived on the store. All-fresh clears grade CORRECT.
        from clouddog import scorecard as _scorecard

        _outcome = _scorecard.classify_delete_outcome(
            True, verdict, verify_fresh=(resumed_chunks == 0)
        )
        if _outcome == _scorecard.NEAR_MISS:
            self._grade(report, _outcome, path, rec.size, self.remote_ref,
                        f"cleared on STALE verify: {resumed_chunks} chunk(s) "
                        "verified in a previous run, not this one")
        else:
            self._grade(report, _outcome, path, rec.size, self.remote_ref,
                        "cleared through delete gate (streamed + live-verified this run + gate passed)",
                        cleared=True)
        rec.status = "cleared"
        save_manifest(manifest)
        manifest.seen.pop(name, None)
        report.files_cleared += 1
        report.freed_bytes += rec.size
        self._journal(
            "cleared",
            path,
            rec.size,
            self.remote_ref,
            f"{name}: local copy cleared after verified stream (delete-as-you-go)",
        )

    # ------------------------------------------------------------------ API

    def stream_once(self, now: float | None = None) -> StreamReport:
        """One pass: settle-scan, then stream every ready file."""
        import uuid

        self._invocation_id = uuid.uuid4().hex[:16]
        report = StreamReport()
        if not os.path.isdir(self.watch_dir):
            report.errors.append(f"watch dir missing: {self.watch_dir}")
            return report
        manifest = load_manifest(self.watch_dir, self.repo, self.tag, self.chunk_bytes)
        ready, skipped = scan_watch(self.watch_dir, manifest, self.settle_secs, now=now)
        report.skipped = skipped
        for path in ready:
            try:
                self._stream_file(manifest, path, report)
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the pass
                report.errors.append(f"{os.path.basename(path)}: crashed: {exc}")
        save_manifest(manifest)
        return report

    def run_forever(self, interval_s: int = 60) -> None:
        """Watch loop: stream_once every interval_s. Ctrl-C exits cleanly."""
        print(
            f"[clouddog] stream watch: dir={self.watch_dir} "
            f"store={self.remote_ref} chunk={self.chunk_bytes}B "
            f"settle={self.settle_secs}s apply={self.apply}",
            flush=True,
        )
        try:
            while True:
                report = self.stream_once()
                if report.files_streamed or report.errors:
                    print(
                        f"[clouddog] stream pass: streamed={report.files_streamed} "
                        f"cleared={report.files_cleared} "
                        f"freed={report.freed_bytes}B errors={len(report.errors)}",
                        flush=True,
                    )
                time.sleep(interval_s)
        except KeyboardInterrupt:
            print("[clouddog] stream watch stopping (keyboard interrupt)")
