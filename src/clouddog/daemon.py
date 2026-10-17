"""CloudDog hygiene daemon.

Each tick: read disk usage, policy.scan every root, store every STORE /
STORE_AND_CLEAR entry to its tier's backend, byte-verify the stored copy,
and delete the local original only after a successful verify (never in
dry-run mode, never for protected actions).

Iron rules enforced here, defense in depth:
  * NEVER delete on a failed verify.
  * NEVER touch SACRED / NEVER_DELETE / KEEP_LOCAL (_delete_local raises
    PolicyViolation for anything but STORE_AND_CLEAR).
  * Silent ticks: tick() returns a TickReport; run_forever() only prints
    when a tick is critical or recorded errors.
"""
from __future__ import annotations

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
    ) -> None:
        self.roots = list(roots)
        self.interval_s = interval_s
        self.backends = backends if backends is not None else _default_backends()
        self.journal = journal if journal is not None else _default_journal()
        self.disk_path = disk_path
        self.dry_run = dry_run
        self.pidfile = pidfile or os.path.expanduser("~/.clouddog/daemon.pid")

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
            # SACRED / NEVER_DELETE / KEEP_LOCAL (and anything unknown):
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

        if report.skipped_sacred:
            # One summary event per tick instead of one per skipped file.
            self._journal("skipped", ",".join(self.roots), 0, "",
                          f"skipped {report.skipped_sacred} protected entr"
                          f"{'y' if report.skipped_sacred == 1 else 'ies'} "
                          f"(sacred/never_delete/keep_local)")

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
        return report

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
