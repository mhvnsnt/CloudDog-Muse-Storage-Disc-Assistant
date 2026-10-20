"""CloudDog hygiene daemon.

Each tick: read disk usage, policy.scan every root, store every STORE /
STORE_AND_CLEAR entry to its tier's backend, byte-verify the stored copy,
and delete the local original only after a successful verify (never in
dry-run mode, never for protected actions).

Iron rules enforced here, defense in depth:
  * NEVER delete on a failed verify.
  * NEVER touch SACRED / STORE_AND_CLEAR_STRICT / KEEP_LOCAL (_delete_local raises
    PolicyViolation for anything but STORE_AND_CLEAR).
  * Silent ticks: tick() returns a TickReport; run_forever() only prints
    when a tick is critical or recorded errors.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from clouddog.errors import CloudDogError, PolicyViolation
from clouddog.stores.base import Health, StoreBackend, StoredRef

# policy.scan returns action as the Action enum's string value.
_STORE_ACTIONS = {"store", "store_and_clear"}
_ONE_GB = 1024 ** 3
_CRITICAL_DISK_PCT = 85.0


@dataclass
class TickReport:
    """Outcome of one daemon tick."""

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


def _default_journal_path() -> str:
    return os.path.expanduser("~/.clouddog/audit.jsonl")


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
    ) -> None:
        self.roots = list(roots)
        self.interval_s = interval_s
        self.backends = backends if backends is not None else _default_backends()
        self.journal = journal if journal is not None else _default_journal()
        self.disk_path = disk_path
        self.dry_run = dry_run
        self.pidfile = pidfile or os.path.expanduser("~/.clouddog/daemon.pid")
        self.heartbeat_path = os.path.expanduser("~/.clouddog/heartbeat.json")
        self.stream_backend = stream_backend
        self.stream_dirs_path = os.path.expanduser("~/.clouddog/stream-dirs.json")

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

    def _record_error(self, report: TickReport, flags: dict, path: str,
                      msg: str, size: int = 0, backend_error: bool = False) -> None:
        report.errors.append(msg)
        self._journal("error", path, size, "", msg)
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
            stored: StoredRef = backend.store(path, remote_ref, dry_run=self.dry_run)
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
                      f"backend={tier} dry_run={self.dry_run}")

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

        if action == "store_and_clear":
            if self.dry_run:
                return  # dry-run: verify the pipeline, delete nothing
            if not backend.supports_content_verify:
                # H2: name+size-only verification is not byte-proof; store
                # but never clear local on such tiers.
                self._journal("skipped", path, size, stored.locator,
                              f"backend={tier} has no content verification; "
                              f"kept local")
                return
            # M5: re-evaluate policy just before delete — the file may have
            # become sacred/in-flight since the scan.
            from clouddog import policy as _policy
            try:
                dec = _policy.evaluate(path)
            except Exception:
                dec = None
            if dec is None or dec.action != _policy.Action.STORE_AND_CLEAR:
                self._journal(
                    "skipped", path, size, stored.locator,
                    "policy changed since scan; kept local")
                return
            # M5: re-stat just before delete; abort if the file moved.
            try:
                if os.path.getsize(path) != size:
                    self._journal("skipped", path, size, stored.locator,
                                  "size changed since verify; kept local")
                    return
            except OSError:
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
            report.cleared += 1
            report.freed_bytes += freed
            self._journal("cleared", path, freed, stored.locator,
                          f"backend={tier} verified then cleared")

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
            live = bool(ent.get("apply", True)) and not self.dry_run
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
                "dry_run": self.dry_run,
                "disk_before_pct": report.disk_before_pct,
                "disk_after_pct": report.disk_after_pct,
                "stored": report.stored,
                "verified": report.verified,
                "cleared": report.cleared,
                "freed_bytes": report.freed_bytes,
                "stream_files": report.stream_files,
                "stream_chunks": report.stream_chunks,
                "stream_freed_bytes": report.stream_freed_bytes,
                "skipped_sacred": report.skipped_sacred,
                "errors": len(report.errors),
                "critical": report.critical,
                "critical_msg": report.critical_msg,
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
                        f"disk {report.disk_before_pct:.1f}%->{report.disk_after_pct:.1f}% "
                        f"stored={report.stored} verified={report.verified} "
                        f"cleared={report.cleared} freed={report.freed_bytes}B "
                        f"skipped_sacred={report.skipped_sacred} "
                        f"errors={len(report.errors)} critical={report.critical}"
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
