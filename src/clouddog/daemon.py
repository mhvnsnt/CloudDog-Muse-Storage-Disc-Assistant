"""CloudDog hygiene daemon.

Each tick: read disk usage, policy.scan every root, store every STORE /
STORE_AND_CLEAR entry to its tier's backend, byte-verify the stored copy,
and delete the local original only after a successful verify (never in
dry-run mode, never for protected actions).

Iron rules enforced here, defense in depth:
  * NEVER delete on a failed verify.
  * NEVER touch SACRED / STORE_AND_CLEAR_STRICT / KEEP_LOCAL (_delete_local raises
    PolicyViolation for anything but STORE_AND_CLEAR).
  * NEVER delete without the DELETE GATE (clouddog.delete_gate, the
    2026-10-20 tripo-wire incident fix): a PRE-EXISTING registry entry
    bound to the local bytes (sha256+size) + the entry's store copy
    LIVE-verified within this run + no live work claim + a fresh
    policy pass. A store this daemon just made is REGISTERED, never
    deleted in the same pass that created its entry (two-pass rule) —
    before this gate existed, the daemon deleted with no registry
    entry and no claims check at all.
  * Silent ticks: tick() returns a TickReport; run_forever() only prints
    when a tick is critical or recorded errors.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from clouddog import registry as _registry_mod
from clouddog.claims import default_claims_db
from clouddog.delete_gate import GateBlock, GateVerdict, check_delete_gate
from clouddog.errors import CloudDogError, PolicyViolation
from clouddog.stores.base import Health, StoreBackend, StoredRef

# policy.scan returns action as the Action enum's string value.
_STORE_ACTIONS = {"store", "store_and_clear"}
_ONE_GB = 1024 ** 3
_CRITICAL_DISK_PCT = 85.0


@dataclass
class TickReport:
    """Outcome of one daemon tick.

    Correctness fields lead (scorecard, owner law 2026-10-20);
    freed_bytes is a footnote and stays last in every surface.
    """

    started_utc: str
    disk_before_pct: float
    disk_after_pct: float
    stored: int
    verified: int
    cleared: int
    freed_bytes: int
    skipped_sacred: int
    stream_files: int = 0
    stream_chunks: int = 0
    stream_freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    critical: bool = False
    critical_msg: str = ""
    # Scorecard / trust (training layer, 2026-10-20)
    correct_clears: int = 0
    correct_blocks: int = 0
    correct_kept: int = 0
    near_misses: int = 0
    violations: int = 0
    unmirrored_old: int = 0
    error_actions: int = 0
    trust_state: str = ""
    trust_forced_dry_run: bool = False

    @property
    def correct(self) -> int:
        return self.correct_clears + self.correct_kept

    @property
    def clean_cycle(self) -> bool:
        """A cycle is clean only with zero violations, zero near-misses,
        and zero unmirrored-old findings (trust promotion input)."""
        return (
            self.violations == 0
            and self.near_misses == 0
            and self.unmirrored_old == 0
        )


def _state_dir() -> str:
    """The one configurable state dir ($CLOUDEDOG_STATE_DIR or
    ~/.clouddog) — trust, heartbeat, journal and stream config all
    resolve through it, never through a repo checkout."""
    from clouddog import trust as _trust

    return _trust.state_dir()


def _default_journal_path() -> str:
    return os.path.join(_state_dir(), "audit.jsonl")


def _default_journal():
    """Build the default AuditJournal (lazy import; in-memory fallback)."""
    try:
        from clouddog.audit import AuditJournal

        path = _default_journal_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return AuditJournal(path)
    except ImportError:
        return _MemoryJournal()


class _MemoryJournal:
    """Minimal journal stand-in used only when clouddog.audit is unavailable."""

    def __init__(self):
        self.events: list = []

    def record(self, event) -> None:
        self.events.append(event)

    def read(self, limit: int = 100) -> list:
        return list(self.events[-limit:])


def _default_backends() -> dict[str, StoreBackend]:
    """Instantiate whichever backends are importable; skip missing ones."""
    backends: dict[str, StoreBackend] = {}
    try:
        from clouddog.stores.github import GitHubReleaseBackend

        backends["github"] = GitHubReleaseBackend()
    except ImportError:
        pass
    try:
        from clouddog.stores.terabox import TeraBoxBackend

        backends["terabox"] = TeraBoxBackend()
    except ImportError:
        pass
    try:
        from clouddog.stores.drive import DriveBackend

        backends["drive"] = DriveBackend()
    except ImportError:
        pass
    return backends


def _sha256_of(path: str) -> str:
    """Streaming sha256 of a local file (delete-gate byte binding)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _size_of(path: str) -> int:
    """Byte size of a file or directory tree (0 if unreadable)."""
    try:
        if os.path.islink(path) or os.path.isfile(path):
            return os.path.getsize(path)
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                fp = os.path.join(dirpath, name)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
        return total
    except OSError:
        return 0


class Daemon:
    """Periodic scan -> store -> verify -> clear loop."""

    def __init__(
        self,
        roots: list[str],
        interval_s: int = 600,
        backends: dict[str, StoreBackend] | None = None,
        journal: "object | None" = None,
        disk_path: str = "/home/hatch",
        dry_run: bool = False,
        pidfile: str | None = None,
        stream_backend: "StoreBackend | None" = None,
        registry_path: str | None = None,
        registry_db_path: str | None = None,
        claims_db: str | None = None,
        trust_path: str | None = None,
        trust_required_streak: int | None = None,
        unmirrored_age_s: int | None = None,
        mirror_detect: bool = True,
    ) -> None:
        self.roots = list(roots)
        self.interval_s = interval_s
        self.backends = backends if backends is not None else _default_backends()
        self.journal = journal if journal is not None else _default_journal()
        self.disk_path = disk_path
        self.dry_run = dry_run
        self.pidfile = pidfile or os.path.join(_state_dir(), "daemon.pid")
        self.heartbeat_path = os.path.join(_state_dir(), "heartbeat.json")
        self.stream_backend = stream_backend
        self.stream_dirs_path = os.path.join(_state_dir(), "stream-dirs.json")
        # Delete-gate state (incident fix): the canonical registry +
        # claims store the gate consults before ANY unlink.
        self.registry_path = registry_path or _registry_mod.default_registry_json()
        self.registry_db_path = registry_db_path or _registry_mod.default_registry_db()
        self.claims_db = claims_db or default_claims_db()
        # Training layer (2026-10-20): earned-autonomy trust state +
        # mirror-coverage detector configuration.
        self.trust_path = trust_path
        self.trust_required_streak = trust_required_streak
        from clouddog.scorecard import DEFAULT_UNMIRRORED_AGE_S

        self.unmirrored_age_s = (
            unmirrored_age_s if unmirrored_age_s is not None else DEFAULT_UNMIRRORED_AGE_S
        )
        self.mirror_detect = mirror_detect
        # Effective dry-run for the current tick: self.dry_run OR the
        # trust gate forcing dry-run (set at tick start).
        self._tick_dry_run = dry_run

    # ------------------------------------------------------------ internals

    def _acquire_pidfile(self) -> bool:
        """Single-instance guard (L3). Returns False when another live
        daemon holds the pidfile."""
        try:
            os.makedirs(os.path.dirname(self.pidfile), exist_ok=True)
            fd = os.open(self.pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                with open(self.pidfile) as fh:
                    old_pid = int(fh.read().strip())
                os.kill(old_pid, 0)  # raises if the process is gone
                return False  # another live daemon holds it
            except (ValueError, OSError, FileNotFoundError):
                pass  # stale pidfile; take it over
            fd = os.open(self.pidfile, os.O_CREAT | os.O_WRONLY | os.O_TRUNC)
        with os.fdopen(fd, "w") as fh:
            fh.write(str(os.getpid()))
        return True

    def _release_pidfile(self) -> None:
        try:
            with open(self.pidfile) as fh:
                if fh.read().strip() == str(os.getpid()):
                    os.remove(self.pidfile)
        except OSError:
            pass

    # ------------------------------------------------------------ internals

    def _utcnow(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _disk_pct(self) -> float:
        try:
            usage = shutil.disk_usage(self.disk_path)
            return (usage.used / usage.total * 100.0) if usage.total else 0.0
        except OSError:
            return 0.0

    def _journal(self, kind: str, path: str, size: int, locator: str, note: str = "") -> None:
        try:
            from clouddog.audit import AuditEvent

            event = AuditEvent(ts=self._utcnow(), kind=kind, path=path,
                               size=size, locator=locator, note=note)
        except ImportError:
            event = (self._utcnow(), kind, path, size, locator, note)
        try:
            self.journal.record(event)
        except Exception:
            pass  # journaling must never break a tick

    # ------------------------------------------------------- scorecard

    def _record_grade(self, report: TickReport, grade: str, path: str,
                      size: int = 0, locator: str = "", note: str = "",
                      blocks=(), count: int = 1, cleared: bool = False,
                      source: str = "daemon") -> None:
        """Grade one action into the scorecard journal + tick counters.

        VIOLATION / NEAR_MISS grades revoke trust on the spot (inside
        scorecard.record_grade). Grading must never break a tick.
        """
        from clouddog import scorecard as _scorecard

        try:
            _scorecard.record_grade(
                self.journal, grade, path=path, source=source, size=size,
                locator=locator, note=note, blocks=blocks,
                trust_path=self.trust_path,
                actor="daemon",
                invocation_id=getattr(self, "_invocation_id", ""),
            )
        except Exception:  # noqa: BLE001 - grading is feedback, not control
            pass
        if grade == _scorecard.CORRECT:
            if cleared:
                report.correct_clears += count
            else:
                report.correct_kept += count
        elif grade == _scorecard.BLOCKED:
            report.correct_blocks += count
        elif grade == _scorecard.NEAR_MISS:
            report.near_misses += count
        elif grade == _scorecard.VIOLATION:
            report.violations += count
        elif grade == _scorecard.UNMIRRORED_OLD:
            report.unmirrored_old += count
        elif grade == _scorecard.ERROR:
            report.error_actions += count

    def _grade_verdict(self, report: TickReport, verdict, path: str,
                       size: int, locator: str, source: str = "daemon") -> str:
        """Grade a delete-gate verdict (journaled; revokes on near-miss)."""
        from clouddog import scorecard as _scorecard

        grade = _scorecard.classify_gate_verdict(verdict)
        self._record_grade(
            report, grade, path, size=size, locator=locator,
            note=verdict.summary, blocks=tuple(verdict.blocks), source=source,
        )
        return grade

    def _trust_state(self) -> str:
        from clouddog import trust as _trust

        try:
            state = _trust.load_trust(
                self.trust_path, required_streak=self.trust_required_streak
            )
            return str(state.get("state") or _trust.REVOKED)
        except Exception:  # noqa: BLE001 - unreadable trust = REVOKED
            return _trust.REVOKED

    def _record_error(self, report: TickReport, flags: dict, path: str,
                      msg: str, size: int = 0, backend_error: bool = False) -> None:
        report.errors.append(msg)
        self._journal("error", path, size, "", msg)
        # ERROR grade (training layer): an action was attempted and
        # failed without deleting — backend down, store/verify failed,
        # file kept. Not correct, not a violation.
        self._record_grade(report, "ERROR", path, size=size, note=msg)
        if backend_error and size > _ONE_GB:
            flags["big_backend_error"] = True

    def _remote_ref(self, tier: str) -> str:
        """Default remote ref per tier; env-overridable, no hardcoded owner."""
        if tier == "github":
            repo = os.environ.get("CLOUDEDOG_GITHUB_REPO", "mhvnsnt/clouddog-storage")
            tag = f"clouddog-{datetime.now(timezone.utc):%Y%m%d}"
            return f"{repo}@{tag}"
        if tier == "terabox":
            return os.environ.get("CLOUDEDOG_TERABOX_DIR", "/clouddog")
        if tier == "drive":
            return os.environ.get("CLOUDEDOG_DRIVE_REF", "drive:/clouddog")
        raise CloudDogError(f"unknown tier '{tier}'")

    def _delete_local(self, path: str, action: str) -> int:
        """Delete a local file/dir. Defense in depth: only STORE_AND_CLEAR
        may reach here; anything else raises PolicyViolation."""
        if str(action).lower() != "store_and_clear":
            raise PolicyViolation(
                f"refusing to delete {path}: action '{action}' forbids deletion"
            )
        abspath = os.path.abspath(path)
        for forbidden in ("/", os.path.expanduser("~"), os.path.abspath(self.disk_path)):
            if abspath == forbidden:
                raise PolicyViolation(
                    f"refusing to delete protected root {path}"
                )
        freed = _size_of(path)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return freed

    # ------------------------------------------------------- delete gate

    def _entry_ref(self, entry: dict, tier: str) -> StoredRef | None:
        """Build the StoredRef a registry entry points at, or None when
        the entry cannot be live-verified by this daemon (entry lives
        on another tier, or records no usable locator). None makes the
        gate's verify leg fail — closed, never open."""
        store = str(entry.get("store") or tier).lower()
        if store != tier:
            return None
        locator = str(entry.get("locator") or "")
        extra: dict = {}
        assets = entry.get("asset_names")
        if isinstance(assets, list) and assets:
            extra["asset_names"] = [str(a) for a in assets]
        if not locator:
            if tier == "github":
                repo = str(entry.get("repo") or "")
                release = str(entry.get("release") or "")
                asset = str(entry.get("asset") or "")
                if not (repo and release and asset):
                    return None
                locator = f"{repo}@{release}/{asset}"
                extra.setdefault("asset_names", [asset])
            elif tier == "drive":
                remote = str(entry.get("remote_dir") or "")
                asset = str(entry.get("asset") or "")
                if not (remote and asset):
                    return None
                locator = f"{remote.rstrip('/')}/{asset}"
            else:
                return None
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        return StoredRef(
            backend=tier,
            locator=locator,
            size=size,
            sha256=str(entry.get("sha256") or ""),
            extra=extra,
        )

    def _run_delete_gate(
        self,
        path: str,
        *,
        tier: str,
        backend: StoreBackend,
        stored: StoredRef,
        policy_action: str,
    ) -> GateVerdict:
        """The hard invariant, evaluated at delete time (incident fix).

        (a) A registry entry must PRE-EXIST this pass. On first sight
        the daemon registers its just-verified store and defers the
        delete to a later pass (two-pass rule) — the exact opposite of
        the incident, where an unregistered tree was deleted outright.
        (b) The entry's store copy is live-verified in THIS run: when
        the copy verified moments ago in _process_entry IS the entry's
        copy (same locator + assets) it counts; otherwise the backend
        verifies against the entry's ref right now.
        (c) Claims are consulted fresh (fail closed when unreadable).
        (d) The M5 policy re-evaluation feeds the policy leg.
        """
        from clouddog.claims import check_claimed

        entry = _registry_mod.lookup_entry(
            path, json_path=self.registry_path, db_path=self.registry_db_path
        )
        if entry is None:
            new_entry: dict = {
                "store": tier,
                "locator": stored.locator,
                "sha256": stored.sha256,
                "size": stored.size,
                "note": "registered by clouddog daemon after verified store; "
                        "delete gated to a later pass (two-pass rule)",
            }
            head, _, asset = stored.locator.rpartition("/")
            repo, _, release = head.partition("@")
            if tier == "github" and repo and release and asset:
                new_entry.update({"repo": repo, "release": release, "asset": asset})
            assets = stored.extra.get("asset_names")
            if isinstance(assets, list) and assets:
                new_entry["asset_names"] = [str(a) for a in assets]
            try:
                _registry_mod.register_entry(
                    path, new_entry, json_path=self.registry_path
                )
                self._journal(
                    "registered", path, stored.size, stored.locator,
                    "daemon: verified store registered; delete deferred to a "
                    "later pass (delete gate: no pre-existing registry entry)",
                )
            except Exception as exc:  # noqa: BLE001 - journaled, gate still blocks
                self._journal(
                    "error", path, stored.size, stored.locator,
                    f"daemon: registry write failed: {exc}",
                )
            return GateVerdict(
                allowed=False,
                blocks=(
                    GateBlock(
                        "registry",
                        "no pre-existing registry entry; store registered "
                        "this pass — delete deferred (two-pass rule)",
                    ),
                ),
            )

        try:
            local_sha = _sha256_of(path)
            local_size = os.path.getsize(path)
        except OSError as exc:
            return GateVerdict(
                allowed=False,
                blocks=(GateBlock("registry", f"cannot stat/hash local file: {exc}"),),
            )

        live_ok = False
        ref = self._entry_ref(entry, tier)
        if ref is not None:
            if (
                ref.locator == stored.locator
                and ref.extra.get("asset_names") == stored.extra.get("asset_names")
            ):
                # The copy verified moments ago in this pass IS the
                # entry's copy — that is a live verification this run.
                live_ok = True
            else:
                try:
                    live_ok = bool(backend.verify(path, ref))
                except Exception:  # noqa: BLE001 - verify failure = not verified
                    live_ok = False

        return check_delete_gate(
            path,
            registry_entry=entry,
            local_sha256=local_sha,
            local_size=local_size,
            live_verify_ok=live_ok,
            claim_check=lambda p: check_claimed(self.claims_db, p),
            policy_action=policy_action,
            deletable_actions=frozenset({"store_and_clear"}),
        )

    def _process_entry(self, report: TickReport, flags: dict, entry: dict) -> None:
        action = str(entry.get("action", "") or "").lower()
        path = str(entry.get("path", "") or "")
        try:
            size = int(entry.get("bytes") or 0)
        except (TypeError, ValueError):
            size = 0
        tier = str(entry.get("tier", "") or "github").lower()

        if not path:
            return
        if action not in _STORE_ACTIONS:
            # SACRED / STORE_AND_CLEAR_STRICT / KEEP_LOCAL (and anything unknown):
            # counted, never touched.
            report.skipped_sacred += 1
            return

        # M3: backends are file-only; directories would error every tick.
        # Skip them quietly (a future directory-tar flow can pick them up).
        if os.path.isdir(path) and not os.path.islink(path):
            report.skipped_sacred += 1
            return

        backend = self.backends.get(tier)
        if backend is None:
            self._record_error(
                report, flags, path,
                f"no backend configured for tier '{tier}'; keeping {path} local",
                size=size, backend_error=True,
            )
            return

        try:
            hs = backend.health()
        except Exception as exc:
            self._record_error(
                report, flags, path,
                f"health probe failed for backend '{tier}': {exc}; keeping {path} local",
                size=size, backend_error=True,
            )
            return
        if hs.health is not Health.LIVE:
            self._record_error(
                report, flags, path,
                f"backend '{tier}' not LIVE (health={hs.health.value}: {hs.reason}); "
                f"keeping {path} local",
                size=size, backend_error=True,
            )
            return

        try:
            remote_ref = self._remote_ref(tier)
            stored: StoredRef = backend.store(path, remote_ref, dry_run=self._tick_dry_run)
        except CloudDogError as exc:
            self._record_error(
                report, flags, path,
                f"store failed for {path} via '{tier}': {exc}",
                size=size, backend_error=True,
            )
            return
        except Exception as exc:  # unexpected crash: keep local, record
            self._record_error(
                report, flags, path,
                f"store crashed for {path} via '{tier}': {exc}",
                size=size, backend_error=True,
            )
            return
        report.stored += 1
        self._journal("stored", path, stored.size, stored.locator,
                      f"backend={tier} dry_run={self._tick_dry_run}")

        try:
            ok = backend.verify(path, stored)
        except Exception as exc:
            self._record_error(
                report, flags, path,
                f"verify raised for {path} via '{tier}': {exc}; NOT deleting",
                size=size,
            )
            flags["verify_failed"] = True
            return
        if not ok:
            self._record_error(
                report, flags, path,
                f"VERIFICATION FAILED for {path} via '{tier}'; NOT deleting",
                size=size,
            )
            flags["verify_failed"] = True
            return
        report.verified += 1
        self._journal("verified", path, stored.size, stored.locator,
                      f"backend={tier}")

        if action == "store":
            # Correctly stored-and-kept: a CORRECT grade, not a deletion
            # (the old loop counted only clears; that was the bug).
            self._record_grade(
                report, "CORRECT", path, size=stored.size,
                locator=stored.locator,
                note="stored and kept local (verified store, no clear requested)",
            )
            return

        if action == "store_and_clear":
            if self._tick_dry_run:
                self._record_grade(
                    report, "CORRECT", path, size=stored.size,
                    locator=stored.locator,
                    note="stored and kept local (dry-run tick; pipeline verified, nothing deleted)",
                )
                return  # dry-run: verify the pipeline, delete nothing
            if not backend.supports_content_verify:
                # H2: name+size-only verification is not byte-proof; store
                # but never clear local on such tiers.
                self._journal("skipped", path, size, stored.locator,
                              f"backend={tier} has no content verification; "
                              f"kept local")
                self._record_grade(
                    report, "CORRECT", path, size=stored.size,
                    locator=stored.locator,
                    note="stored and kept local (backend has no content verification)",
                )
                return
            # M5: re-evaluate policy just before delete — the file may have
            # become sacred/in-flight since the scan. The verdict feeds
            # the delete gate's policy leg (evaluate() raising = no
            # verdict = the gate blocks, fail closed).
            from clouddog import policy as _policy
            try:
                dec = _policy.evaluate(path)
                policy_action = dec.action.value if dec is not None else ""
            except Exception:
                policy_action = ""
            # DELETE GATE (2026-10-20 incident fix): no unlink unless a
            # pre-existing registry entry binds to these bytes, the
            # entry's store copy was live-verified in this run, claims
            # are clear, and the fresh policy verdict still allows it.
            # Every block is journaled with its reasons — the incident
            # delete left no journal trace at all.
            verdict = self._run_delete_gate(
                path, tier=tier, backend=backend, stored=stored,
                policy_action=policy_action,
            )
            if not verdict.allowed:
                self._journal("delete_blocked", path, size, stored.locator,
                              verdict.summary)
                # Scorecard: a multi-leg stop is a CORRECT BLOCK; a
                # single-leg stop is a NEAR-MISS and revokes trust.
                self._grade_verdict(report, verdict, path, size, stored.locator)
                return
            try:
                freed = self._delete_local(path, action)
            except PolicyViolation:
                raise
            except Exception as exc:
                self._record_error(
                    report, flags, path,
                    f"local delete failed for {path} after verified store: {exc}",
                    size=size,
                )
                return
            try:
                _registry_mod.mark_reaped(path, json_path=self.registry_path)
            except Exception as exc:  # noqa: BLE001 - delete stands; say so
                self._journal("error", path, freed, stored.locator,
                              f"post-delete registry mark_reaped failed: {exc}")
            report.cleared += 1
            report.freed_bytes += freed
            self._journal("cleared", path, freed, stored.locator,
                          f"backend={tier} verified then cleared (delete gate passed)")
            self._record_grade(
                report, "CORRECT", path, size=freed, locator=stored.locator,
                note="cleared through delete gate (stored + live-verified + gate passed)",
                cleared=True,
            )

    def _stream_journal(self):
        """Journal wrapper for stream passes: normal audit journal plus the
        human-readable disk-cleanup log on every 'cleared' (repo rule 7).
        """
        def _fn(kind: str, path: str, size: int, locator: str,
                note: str = "") -> None:
            self._journal(kind, path, size, locator, note)
            if kind == "cleared":
                try:
                    from clouddog.cli import log_deletion_md
                except ImportError:
                    return  # journaling must never break a stream
                log_deletion_md(path, size, locator, note)
        return _fn

    def _run_stream_dirs(self, report: TickReport, flags: dict) -> None:
        """Config-file-driven watch-dir stream passes.

        ``~/.clouddog/stream-dirs.json`` (read every tick; the running
        daemon needs no restart to pick up a new watch dir):
            [{"watch_dir": "/path", "repo": "o/r", "tag": "t",
              "tier": "github", "chunk_mb": 100, "settle_secs": 300,
              "apply": true}]
        Absent file = no-op. apply is AND-ed with the daemon's dry_run.
        tier defaults to github; tiers without content verification
        (TeraBox) are refused for live streams (H2). Any per-dir failure
        is journaled; a bad dir never breaks the tick.
        """
        from clouddog import streamer as _streamer

        try:
            with open(self.stream_dirs_path, encoding="utf-8") as fh:
                entries = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            self._journal("error", self.stream_dirs_path, 0, "",
                          f"bad stream-dirs.json: {exc}")
            return
        if not isinstance(entries, list):
            self._journal("error", self.stream_dirs_path, 0, "",
                          "stream-dirs.json must be a JSON list")
            return
        backend = self.stream_backend
        if backend is None:
            try:
                from clouddog.stores.github import GitHubReleaseBackend

                backend = GitHubReleaseBackend()
            except ImportError:
                self._journal("error", self.stream_dirs_path, 0, "",
                              "github backend unavailable; stream dirs skipped")
                return
        try:
            hs = backend.health()
        except Exception as exc:
            self._journal("error", self.stream_dirs_path, 0, "",
                          f"stream backend health probe failed: {exc}")
            return
        if hs.health is not Health.LIVE:
            self._journal("error", self.stream_dirs_path, 0, "",
                          f"stream backend not LIVE (health={hs.health.value}); "
                          f"stream dirs skipped")
            return
        journal_fn = self._stream_journal()
        for ent in entries:
            if not isinstance(ent, dict) or not ent.get("watch_dir"):
                continue
            _chunk_mb = ent.get("chunk_mb")
            _settle = ent.get("settle_secs")
            live = bool(ent.get("apply", True)) and not self._tick_dry_run
            if live and not backend.supports_content_verify:
                # H2: name+size-only verification is not byte-proof; store
                # but never clear the local copy on such tiers.
                self._journal("error", str(ent.get("watch_dir")), 0, "",
                              "stream refused: backend has no content "
                              "verification; kept local")
                continue
            try:
                st = _streamer.Streamer(
                    str(ent["watch_dir"]),
                    backend,
                    repo=str(ent.get("repo") or _streamer.DEFAULT_REPO),
                    tag=ent.get("tag"),
                    chunk_bytes=int(100 if _chunk_mb is None else _chunk_mb)
                    * 1024
                    * 1024,
                    settle_secs=int(300 if _settle is None else _settle),
                    apply=live,
                    journal=journal_fn,
                    trust_path=self.trust_path,
                    scorecard_journal=self.journal,
                )
                rep = st.stream_once()
            except Exception as exc:  # noqa: BLE001 - one dir must not break the tick
                self._record_error(report, flags, str(ent.get("watch_dir")),
                                   f"stream-dir pass failed: {exc}")
                continue
            # chunks_uploaded counts chunks stored+byte-verified.
            report.stored += rep.chunks_uploaded
            report.verified += rep.chunks_uploaded
            report.cleared += rep.files_cleared
            report.freed_bytes += rep.freed_bytes
            report.stream_files += rep.files_streamed
            report.stream_chunks += rep.chunks_uploaded
            report.stream_freed_bytes += rep.freed_bytes
            # Scorecard aggregation from the stream pass.
            report.correct_clears += getattr(rep, "correct_clears", 0)
            report.correct_blocks += getattr(rep, "correct_blocks", 0)
            report.correct_kept += getattr(rep, "correct_kept", 0)
            report.near_misses += getattr(rep, "near_misses", 0)
            report.violations += getattr(rep, "violations", 0)
            report.error_actions += getattr(rep, "error_actions", 0)
            for e in rep.errors:
                self._record_error(report, flags, str(ent.get("watch_dir")),
                                   f"stream: {e}")

    # ------------------------------------------------------------------ API

    def tick(self) -> TickReport:
        """Run one sweep. Never raises for per-entry/per-root failures."""
        from clouddog import policy  # lazy: built in parallel

        report = TickReport(
            started_utc=self._utcnow(),
            disk_before_pct=self._disk_pct(),
            disk_after_pct=0.0,
            stored=0,
            verified=0,
            cleared=0,
            freed_bytes=0,
            skipped_sacred=0,
        )
        flags = {"verify_failed": False, "big_backend_error": False}

        # Attribution: one invocation id per tick, stamped on every
        # graded record this tick produces.
        import uuid

        self._invocation_id = uuid.uuid4().hex[:16]

        # TRUST GATE (earned autonomy, 2026-10-20): checked before
        # EVERY tick. Launched with --apply while trust != APPLY ->
        # this tick is forced to dry-run and the refusal is journaled
        # (a CORRECT BLOCK: the training layer stopping an unearned
        # privilege). Initialized state is REVOKED, so a fresh daemon
        # can never delete on its first tick.
        trust_state = self._trust_state()
        report.trust_state = trust_state
        self._tick_dry_run = self.dry_run
        if not self.dry_run and trust_state != "APPLY":
            self._tick_dry_run = True
            report.trust_forced_dry_run = True
            self._journal(
                "trust_refused_apply", "", 0, "",
                f"daemon launched with --apply while trust_state={trust_state}; "
                "forced dry-run for this tick (earned autonomy)",
            )
            self._record_grade(
                report, "BLOCKED", "", note=(
                    f"trust gate refused --apply (trust_state={trust_state}); "
                    "tick forced to dry-run"
                ),
                source="trust",
            )

        for root in self.roots:
            try:
                entries = policy.scan(root)
            except Exception as exc:
                self._record_error(report, flags, root,
                                   f"policy scan failed for {root}: {exc}")
                continue
            for entry in entries or []:
                try:
                    self._process_entry(report, flags, entry)
                except PolicyViolation:
                    # Defense in depth fired: record and keep going.
                    self._record_error(report, flags, str(entry.get("path", "")),
                                       f"POLICY VIOLATION blocked for {entry.get('path', '')}")
                except Exception as exc:
                    self._record_error(report, flags, str(entry.get("path", "")),
                                       f"unexpected error on {entry.get('path', '')}: {exc}")

        self._run_stream_dirs(report, flags)

        if report.skipped_sacred:
            # One summary event per tick instead of one per skipped file.
            self._journal("skipped", ",".join(self.roots), 0, "",
                          f"skipped {report.skipped_sacred} protected entr"
                          f"{'y' if report.skipped_sacred == 1 else 'ies'} "
                          f"(sacred/store_and_clear_strict/keep_local)")
            # Correctly kept-local is CORRECT behavior — grade it.
            self._record_grade(
                report, "CORRECT", ",".join(self.roots),
                note=f"kept local: {report.skipped_sacred} protected "
                     "entries untouched (sacred/strict/keep_local)",
                count=report.skipped_sacred,
            )

        # MIRROR-COVERAGE DETECTOR (2026-10-20 casualty lesson): old
        # work/scratch/intermediate files with no registry entry and no
        # store record are a system FAILURE STATE, journaled as
        # UNMIRRORED_OLD scorecard records and counted at the top of
        # every summary — never a footnote. A tick containing findings
        # is not a clean cycle for trust promotion.
        if self.mirror_detect:
            try:
                from clouddog import scorecard as _scorecard
                from clouddog.claims import check_claimed

                findings = _scorecard.detect_unmirrored_old(
                    self.roots,
                    age_s=self.unmirrored_age_s,
                    registry_lookup=lambda p: _registry_mod.lookup_entry(
                        p, json_path=self.registry_path,
                        db_path=self.registry_db_path),
                    claim_check=lambda p: check_claimed(self.claims_db, p),
                    max_depth=6,
                )
            except Exception as exc:  # noqa: BLE001 - detector must not break a tick
                findings = []
                self._journal("error", "", 0, "",
                              f"mirror-coverage detector failed: {exc}")
            for f in findings:
                self._journal("unmirrored_old", f["path"], f["size"], "",
                              f["reason"])
                self._record_grade(
                    report, "UNMIRRORED_OLD", f["path"], size=f["size"],
                    note=f["reason"], source="daemon-mirror-detector",
                )

        # TRUST CYCLE UPDATE: feed a clean cycle, or record why this
        # cycle cannot promote. Violations/near-misses already revoked
        # at grade time (scorecard.record_grade persists the revocation
        # immediately); this step only handles the promotion streak.
        try:
            from clouddog import trust as _trust_mod

            if report.violations or report.near_misses:
                pass  # already REVOKED at grade time; streak is zero
            elif report.unmirrored_old:
                _trust_mod.record_unclean_cycle(
                    self.trust_path, source="daemon",
                    reason="unmirrored_old findings in cycle; streak reset",
                )
            else:
                _trust_mod.record_clean_cycle(self.trust_path, source="daemon")
            report.trust_state = _trust_mod.current_state(self.trust_path)
        except Exception:  # noqa: BLE001 - trust bookkeeping never breaks a tick
            report.trust_state = self._trust_state()

        report.disk_after_pct = self._disk_pct()

        causes: list[str] = []
        if report.disk_after_pct > _CRITICAL_DISK_PCT:
            causes.append(
                f"disk at {report.disk_after_pct:.1f}% "
                f"(>{_CRITICAL_DISK_PCT:.0f}%)"
            )
        if flags["verify_failed"]:
            causes.append("verification failure(s) during tick")
        if flags["big_backend_error"]:
            causes.append("backend error on a >1GB file")
        report.critical = bool(causes)
        report.critical_msg = "; ".join(causes)
        self._write_heartbeat(report)
        return report

    def _write_heartbeat(self, report: TickReport) -> None:
        """Best-effort liveness record after every tick (supervisor reads
        this; a stale heartbeat means the daemon is wedged or dead).
        Heartbeat failures must never break a tick."""
        try:
            os.makedirs(os.path.dirname(self.heartbeat_path), exist_ok=True)
            payload = {
                "ts_utc": self._utcnow(),
                "pid": os.getpid(),
                "roots": list(self.roots),
                "interval_s": self.interval_s,
                "dry_run": self._tick_dry_run,
                "disk_before_pct": report.disk_before_pct,
                "disk_after_pct": report.disk_after_pct,
                # Correctness FIRST (scorecard, 2026-10-20); bytes last.
                "correct_clears": report.correct_clears,
                "correct_blocks": report.correct_blocks,
                "correct_kept": report.correct_kept,
                "near_misses": report.near_misses,
                "violations": report.violations,
                "unmirrored_old": report.unmirrored_old,
                "error_actions": report.error_actions,
                "trust_state": report.trust_state,
                "trust_forced_dry_run": report.trust_forced_dry_run,
                "stored": report.stored,
                "verified": report.verified,
                "cleared": report.cleared,
                "skipped_sacred": report.skipped_sacred,
                "errors": len(report.errors),
                "critical": report.critical,
                "critical_msg": report.critical_msg,
                "stream_files": report.stream_files,
                "stream_chunks": report.stream_chunks,
                "stream_freed_bytes": report.stream_freed_bytes,
                "freed_bytes": report.freed_bytes,
            }
            tmp = self.heartbeat_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.heartbeat_path)
        except OSError:
            pass

    def run_forever(self) -> None:
        """Loop tick() every interval_s. Prints only when critical or on error."""
        if not self._acquire_pidfile():
            print(f"[clouddog] another daemon is already running "
                  f"(pidfile {self.pidfile}); exiting")
            return
        print(f"[clouddog] daemon starting: roots={self.roots} "
              f"interval={self.interval_s}s dry_run={self.dry_run}")
        try:
            while True:
                try:
                    report = self.tick()
                except KeyboardInterrupt:
                    print("[clouddog] daemon stopping (keyboard interrupt)")
                    break
                except Exception as exc:  # a tick must never kill the daemon
                    print(f"[clouddog] tick crashed: {exc}")
                    try:
                        time.sleep(self.interval_s)
                    except KeyboardInterrupt:
                        print("[clouddog] daemon stopping (keyboard interrupt)")
                        break
                    continue

                if report.critical or report.errors:
                    print(
                        f"[clouddog] tick @ {report.started_utc}: "
                        f"correct_clears={report.correct_clears} "
                        f"correct_blocks={report.correct_blocks} "
                        f"near_misses={report.near_misses} "
                        f"violations={report.violations} "
                        f"unmirrored_old={report.unmirrored_old} "
                        f"trust_state={report.trust_state} "
                        f"disk {report.disk_before_pct:.1f}%->{report.disk_after_pct:.1f}% "
                        f"stored={report.stored} verified={report.verified} "
                        f"cleared={report.cleared} "
                        f"skipped_sacred={report.skipped_sacred} "
                        f"errors={len(report.errors)} critical={report.critical} "
                        f"freed={report.freed_bytes}B"
                    )
                    for err in report.errors:
                        print(f"[clouddog]   error: {err}")
                    if report.critical:
                        print(f"[clouddog]   CRITICAL: {report.critical_msg}")
                try:
                    time.sleep(self.interval_s)
                except KeyboardInterrupt:
                    print("[clouddog] daemon stopping (keyboard interrupt)")
                    break
        finally:
            self._release_pidfile()
