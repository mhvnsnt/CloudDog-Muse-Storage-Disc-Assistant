"""DELETE-AS-YOU-GO reaper, generalized from the scooby blob_reaper lane.

Owner law (2026-10-17): delete each local file the moment its blob verifies
hash-identical on the store, so disk falls DURING the upload, not after.
The ONE-STORE RULE applies per file: a local copy may be cleared only when
the store holds a byte-identical copy (manifest SHA == locally computed SHA).

SAFETY (non-negotiable):
- Only deletes files strictly UNDER src_dir whose local hash matches the
  manifest-recorded SHA.
- NEVER deletes: the manifest/log/state files, anything matching
  protected_suffixes, anything outside src_dir (including symlink escapes
  and "../" or absolute-path manifest entries).
- Mismatches are logged, never deleted.
- A failed spot_check HALTS the reaper (state saved first).
- DELETE GATE (2026-10-20 tripo-wire incident fix): the unlink passes
  clouddog.delete_gate with all four legs evaluated at delete time —
  manifest entry binding (the reaper's registry record), the live
  store_verify from THIS run, a fresh claims check (wired by default;
  an unreadable claims store fails closed), and a fresh policy
  evaluate() (SACRED always blocks; game files block — the reaper
  cannot attest the strict bar; tooling KEEP_LOCAL blocks).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class ReapReport:
    """Result of one reap_once pass."""

    reaped: int = 0
    mismatches: int = 0
    skipped: int = 0
    freed_bytes: int = 0
    halted: bool = False
    halt_reason: str = ""


def compute_hash(path: str, algo: str = "git-sha1") -> str:
    """Compute a file hash, streaming in 8 MiB chunks (safe for multi-GB blobs).

    algo "git-sha1": sha1("blob <len>\\0" + data) — matches the GitHub
    git-blobs API SHA. algo "sha256": plain sha256 — matches release
    asset checksums.
    """
    if algo == "git-sha1":
        h = hashlib.sha1()
        h.update(b"blob %d\0" % os.path.getsize(path))
    elif algo == "sha256":
        h = hashlib.sha256()
    else:
        raise ValueError(f"unknown hash_algo: {algo!r}")
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_policy_action(path: str) -> str:
    """Map a fresh policy.evaluate() verdict to the reaper's gate action.

    The reaper's deletable set is {store, store_and_clear}: the lane
    manifest + live store verify are its authorization to clear, so the
    hot/small KEEP_LOCAL *scan heuristic* maps to "store" (superseded,
    not a safety verdict). The safety verdicts pass through untouched
    and therefore block at the gate: SACRED (in-flight / marked /
    freshly modified), STORE_AND_CLEAR_STRICT (game files — the reaper
    cannot attest retrievability + owner surfacing), and KEEP_LOCAL for
    tooling paths (tools/ or .git/). evaluate() failing maps to ""
    (no verdict), which the gate treats as a block — fail closed.
    """
    try:
        from clouddog import policy

        dec = policy.evaluate(path)
    except Exception:  # noqa: BLE001 - no verdict = block at the gate
        return ""
    if dec.action == policy.Action.KEEP_LOCAL:
        from pathlib import Path

        parts = {p.lower() for p in Path(path).parts}
        if parts & policy.TOOL_COMPONENTS:
            return "keep_local"
        return "store"
    return dec.action.value


class Reaper:
    """Verified-delete engine.

    manifest maps rel-path (relative to src_dir) -> expected hex digest.
    Only manifest-listed files are ever candidates for deletion; everything
    else under src_dir is ignored.

    Deletion requires TWO proofs: (1) the local file hashes to the
    manifest-recorded SHA (lane accounting), and (2) ``store_verify`` —
    a caller-supplied callback that checks the file against the LIVE
    store (``backend.verify``). The manifest is a local claim; the live
    store check is the proof. If ``store_verify`` is None, live runs
    refuse to delete (fail closed); dry runs are unaffected.
    """

    def __init__(
        self,
        src_dir: str,
        manifest: dict[str, str],
        log_path: str,
        state_path: str | None = None,
        hash_algo: str = "git-sha1",
        spot_check: Callable[[str, str], bool] | None = None,
        spot_every: int = 20,
        store_verify: Callable[[str, str], bool] | None = None,
        protected_suffixes: tuple[str, ...] = (".py", ".log", ".json", ".sha256"),
        protected_names: tuple[str, ...] | None = None,
        claim_check: Callable[[str], tuple[bool, str]] | None = None,
        claims_db: str | None = None,
        policy_check: Callable[[str], str] | None = None,
    ) -> None:
        # claim_check (the Tekken 5 fix): callable consulted before any
        # delete; takes the guarded absolute path, returns (blocked,
        # reason). Blocked => never deleted; a checker that raises fails
        # closed (skip). When not supplied, the production checker is
        # wired by default (clouddog.claims.make_claim_checker over
        # `claims_db` or the canonical claims store) — since the
        # 2026-10-20 incident fix there is NO gate-less mode: a reaper
        # that cannot consult claims must not delete.
        if claim_check is None:
            from clouddog.claims import make_claim_checker

            claim_check = make_claim_checker(claims_db)
        self._claim_check = claim_check
        # policy_check: callable(path) -> gate action string for a FRESH
        # policy verdict at delete time (see _default_policy_action).
        # None = the default mapping. A checker that raises fails closed
        # at the gate call site.
        self._policy_check = policy_check or _default_policy_action
        if hash_algo not in ("git-sha1", "sha256"):
            raise ValueError(f"unknown hash_algo: {hash_algo!r}")
        src_real = os.path.realpath(src_dir)
        if not os.path.isdir(src_real):
            raise FileNotFoundError(f"src_dir missing: {src_dir}")
        self._src_dir = src_real
        self._manifest = dict(manifest)
        self._log_path = log_path
        self._state_path = state_path
        self._hash_algo = hash_algo
        self._spot_check = spot_check
        self._spot_every = max(1, int(spot_every))
        self._store_verify = store_verify
        self._protected_suffixes = tuple(protected_suffixes)
        names: set[str] = set(protected_names or ())
        for p in (log_path, state_path):
            if p:
                names.add(os.path.basename(p))
        self._protected_names = frozenset(names)

    # -- logging ----------------------------------------------------------

    def log(self, msg: str) -> None:
        """Write a timestamped line to the log (flush+fsync) and print it."""
        line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line, flush=True)
        os.makedirs(os.path.dirname(os.path.abspath(self._log_path)), exist_ok=True)
        with open(self._log_path, "a") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # -- state ------------------------------------------------------------

    def _load_state(self) -> tuple[set[str], int]:
        if not self._state_path:
            return set(), 0
        try:
            with open(self._state_path) as f:
                d = json.load(f)
            return set(d.get("reaped", [])), int(d.get("spot_check_count", 0))
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return set(), 0

    def _save_state(self, reaped: set[str], spot_count: int) -> None:
        if not self._state_path:
            return
        tmp = self._state_path + ".tmp"
        os.makedirs(os.path.dirname(os.path.abspath(tmp)), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(
                {"reaped": sorted(reaped), "spot_check_count": spot_count}, f
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._state_path)

    # -- guards -----------------------------------------------------------

    def _candidate_path(self, rel: str) -> str | None:
        """Resolved, guarded absolute path for a manifest entry.

        Returns None when the entry must never be touched: absolute paths,
        escapes above src_dir (including ".." tricks and symlink escapes),
        non-files, and protected names/suffixes.
        """
        if not rel or os.path.isabs(rel):
            return None
        full = os.path.realpath(os.path.join(self._src_dir, rel))
        if not (full == self._src_dir or full.startswith(self._src_dir + os.sep)):
            return None
        if not os.path.isfile(full):
            return None
        base = os.path.basename(full)
        if base in self._protected_names:
            return None
        if base.endswith(self._protected_suffixes):
            return None
        return full

    def verify_file(self, rel: str) -> bool:
        """True iff the manifest entry exists, passes guards, and the hash matches."""
        full = self._candidate_path(rel)
        if full is None:
            return False
        expected = self._manifest.get(rel)
        if not expected:
            return False
        try:
            return compute_hash(full, self._hash_algo) == expected
        except OSError:
            return False

    def _prune_empty_parents(self, full: str) -> None:
        """Remove newly-empty parent dirs, strictly under src_dir."""
        d = os.path.dirname(os.path.realpath(full))
        while d.startswith(self._src_dir + os.sep) and d != self._src_dir:
            try:
                os.rmdir(d)  # only succeeds if empty
            except OSError:
                break
            self.log("PRUNED-EMPTY-DIR %s" % os.path.relpath(d, self._src_dir))
            d = os.path.dirname(d)

    # -- reaping ----------------------------------------------------------

    def _try_reap(
        self,
        rel: str,
        sha: str,
        reaped: set[str],
        spot_count: int,
        dry_run: bool,
    ) -> tuple[str, int]:
        """Attempt one file. Returns (action, spot_count).

        action in: "reaped", "mismatch", "skipped".
        """
        full = os.path.join(self._src_dir, rel)
        if not os.path.exists(full):
            # Already gone (e.g. deleted by a previous run before state was
            # written): mark so it is never retried.
            reaped.add(rel)
            return "skipped", spot_count
        guarded = self._candidate_path(rel)
        if guarded is None:
            self.log("GUARD-SKIP %s (protected/outside src_dir)" % rel)
            return "skipped", spot_count
        try:
            local_sha = compute_hash(guarded, self._hash_algo)
        except OSError as e:
            self.log("READ-ERROR %s (%s)" % (rel, e))
            return "skipped", spot_count
        if local_sha != sha:
            self.log(
                "MISMATCH %s (local=%s recorded=%s) NOT deleted"
                % (rel, local_sha, sha)
            )
            return "mismatch", spot_count
        size = os.path.getsize(guarded)
        # Claims gate (the Tekken 5 fix): a live work claim on this path
        # (or an ancestor dir) is a HARD BLOCK. Checked before the
        # dry-run branch so a dry run never prints WOULD-REAP for a file
        # a live run would refuse. A checker that raises fails CLOSED,
        # same as the store_verify contract below.
        if self._claim_check is not None:
            try:
                claimed, why = self._claim_check(guarded)
            except Exception as e:  # noqa: BLE001
                self.log("CLAIMS-UNREADABLE %s (%s) NOT deleted" % (rel, e))
                return "skipped", spot_count
            if claimed:
                self.log("CLAIMED-SKIP %s (%s) NOT deleted" % (rel, why))
                return "skipped", spot_count
        if dry_run:
            self.log("WOULD-REAP %s %s %d" % (rel, sha, size))
            return "skipped", spot_count
        # Live-store proof (C1): the manifest is a local claim. Without a
        # caller-supplied live verification, refuse to delete (fail closed).
        if self._store_verify is None:
            self.log("NO-STORE-VERIFY %s (no live store check wired) NOT deleted" % rel)
            return "skipped", spot_count
        try:
            live_ok = self._store_verify(rel, guarded)
        except Exception as e:  # noqa: BLE001
            self.log("STORE-VERIFY-ERROR %s (%s) NOT deleted" % (rel, e))
            return "skipped", spot_count
        if not live_ok:
            self.log("STORE-VERIFY-FAILED %s NOT deleted" % rel)
            return "mismatch", spot_count
        # TOCTOU re-check (M5): re-stat right before delete; abort on change.
        try:
            st = os.stat(guarded)
        except OSError as e:
            self.log("READ-ERROR %s (%s)" % (rel, e))
            return "skipped", spot_count
        if st.st_size != size:
            self.log("CHANGED-SINCE-HASH %s (size moved) NOT deleted" % rel)
            return "skipped", spot_count
        # DELETE GATE (2026-10-20 incident fix): final choke point at
        # the unlink, all four legs evaluated NOW — the manifest entry
        # (registry leg, digest-bound by the hash check above), the
        # live store_verify from THIS run (verify leg), a FRESH claims
        # check (claims leg), and a fresh policy verdict (policy leg).
        from clouddog.delete_gate import check_delete_gate

        try:
            gate_policy_action = self._policy_check(guarded)
        except Exception:  # noqa: BLE001 - no verdict = block at the gate
            gate_policy_action = ""
        verdict = check_delete_gate(
            guarded,
            registry_entry={"digest": sha, "digest_algo": self._hash_algo,
                            "size": size, "status": "registered"},
            local_digest=local_sha,
            local_digest_algo=self._hash_algo,
            local_size=size,
            live_verify_ok=True,
            claim_check=self._claim_check,
            policy_action=gate_policy_action,
            deletable_actions=frozenset({"store", "store_and_clear"}),
        )
        if not verdict.allowed:
            self.log("DELETE-BLOCKED %s (%s) NOT deleted" % (rel, verdict.summary))
            return "skipped", spot_count
        os.remove(guarded)
        reaped.add(rel)
        spot_count += 1
        self.log("REAPED %s %s %d" % (rel, sha, size))
        self._prune_empty_parents(guarded)
        if self._spot_check is not None and spot_count % self._spot_every == 0:
            if not self._spot_check(rel, sha):
                self._save_state(reaped, spot_count)
                self.log("REAPER HALTED after failed spot-check %s %s" % (rel, sha))
                raise _Halted(f"spot_check failed for {rel} {sha}")
        return "reaped", spot_count

    def reap_once(self, dry_run: bool = False) -> ReapReport:
        """Single pass over manifest entries not yet reaped. Never raises on halt."""
        report = ReapReport()
        reaped, spot_count = self._load_state()
        for rel, sha in self._manifest.items():
            if rel in reaped:
                report.skipped += 1
                continue
            full = os.path.join(self._src_dir, rel)
            existed = os.path.exists(full)
            before_size = os.path.getsize(full) if existed else 0
            try:
                action, spot_count = self._try_reap(
                    rel, sha, reaped, spot_count, dry_run
                )
            except _Halted as e:
                report.halted = True
                report.halt_reason = str(e)
                break
            if action == "reaped":
                report.reaped += 1
                report.freed_bytes += before_size
            elif action == "mismatch":
                report.mismatches += 1
            else:
                report.skipped += 1
        self._save_state(reaped, spot_count)
        return report

    def run_loop(self, interval_s: int = 60, dry_run: bool = False) -> None:
        """reap_once in a loop, with a CYCLE summary line; Ctrl-C exits cleanly."""
        self.log(
            "=== clouddog reaper start (pid %d) SRC=%s dry_run=%s ==="
            % (os.getpid(), self._src_dir, dry_run)
        )
        try:
            while True:
                report = self.reap_once(dry_run=dry_run)
                self.log(
                    "CYCLE done: reaped=%d mismatch=%d skipped=%d freed_bytes=%d halted=%s"
                    % (
                        report.reaped,
                        report.mismatches,
                        report.skipped,
                        report.freed_bytes,
                        report.halted,
                    )
                )
                if report.halted:
                    self.log("REAPER STOPPED: %s" % report.halt_reason)
                    break
                time.sleep(interval_s)
        except KeyboardInterrupt:
            self.log("reaper interrupted (KeyboardInterrupt); exiting cleanly")


class _Halted(Exception):
    """Internal: a failed spot_check stopped the pass mid-loop."""
