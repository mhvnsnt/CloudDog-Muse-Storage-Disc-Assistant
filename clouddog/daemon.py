"""CloudDog daemon: a single mirror pass designed for cron.

``run_once`` never deletes anything. It scans the configured roots, takes at
most the top 5 candidates by size, skips anything in-flight or marked
.DO_NOT_DELETE, and mirrors each remaining candidate to the first configured
store -- but only if the journal does not already record it as stored.
Every action (or failure) is journaled or noted. A 10-minute cron calls
``clouddog watch --once``, which runs :func:`run_loop` with ``once=True``.
"""

from __future__ import annotations

import dataclasses
import os
import time
from datetime import datetime, timezone

from clouddog import config as config_mod
from clouddog import stores as stores_mod
from clouddog.core import inventory, manifest, monitor
from clouddog.policy import laws

#: Maximum candidates mirrored per run. Keeps a 10-minute cron pass bounded.
MAX_CANDIDATES = 5


def _utcnow() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _first_store_name(cfg: config_mod.Config) -> str | None:
    """Name of the first configured store, or None when none is configured."""
    names = list((cfg.stores or {}).keys())
    return names[0] if names else None


def run_once(config: config_mod.Config) -> dict:
    """Run one daemon mirror pass and return a report dict.

    Report keys: ``ts``, ``disk`` (root -> DiskState-as-dict), ``candidates``
    (number considered), ``mirrored`` (paths mirrored this run),
    ``journal_entries`` (count after the run), ``notes`` (human strings).
    Never deletes local files.
    """
    ts = _utcnow()
    notes: list[str] = []

    disk: dict[str, dict] = {}
    for root in config.scan_roots:
        try:
            disk[root] = dataclasses.asdict(monitor.disk_usage(root))
        except OSError as exc:
            notes.append(f"disk_usage failed for {root}: {exc}")

    journal = manifest.Journal(config.journal_path)
    stored_paths: set[str] = set()
    try:
        for entry in journal.entries():
            if entry.action == "store" and not entry.dry_run:
                stored_paths.add(entry.path)
    except OSError as exc:
        notes.append(f"journal read failed: {exc}")

    candidates: list[inventory.Candidate] = []
    for root in config.scan_roots:
        try:
            candidates.extend(inventory.scan(root))
        except OSError as exc:
            notes.append(f"scan failed for {root}: {exc}")
    candidates.sort(key=lambda c: c.size_bytes, reverse=True)
    top = candidates[:MAX_CANDIDATES]

    mirrored: list[str] = []
    store_name = _first_store_name(config)
    for cand in top:
        in_progress, reason = laws.is_in_progress(cand.path)
        if in_progress:
            notes.append(f"skip in-progress {cand.path}: {reason}")
            continue
        if laws.has_do_not_delete_marker(cand.path):
            notes.append(f"skip {cand.path}: DO_NOT_DELETE marker on an ancestor")
            continue
        if cand.path in stored_paths:
            notes.append(f"already mirrored (journal): {cand.path}")
            continue
        if not os.path.isfile(cand.path):
            notes.append(f"skip {cand.path}: v1 mirrors files only (kind {cand.kind})")
            continue
        if store_name is None:
            notes.append("no store configured; cannot mirror")
            break
        try:
            store = stores_mod.get_store(store_name, config.stores or {})
            remote_dir = config.terabox_remote_root if store_name == "terabox" else ""
            ref = stores_mod.run_with_retries(
                lambda s=store, p=cand.path, d=remote_dir: s.upload(p, remote_dir=d),
                what=f"daemon upload {cand.path} to {store_name}",
            )
            if store.verify(ref):
                journal.append(
                    manifest.JournalEntry(
                        ts=ts,
                        action="store",
                        path=cand.path,
                        size_bytes=cand.size_bytes,
                        sha256=ref.sha256,
                        stores=[dataclasses.asdict(ref)],
                        approved_by="daemon",
                        dry_run=False,
                        note=f"daemon mirror to {store_name}",
                    )
                )
                mirrored.append(cand.path)
            else:
                notes.append(f"verify FAILED for {cand.path} on {store_name}")
                journal.append(
                    manifest.JournalEntry(
                        ts=ts,
                        action="verify",
                        path=cand.path,
                        size_bytes=cand.size_bytes,
                        sha256=ref.sha256,
                        stores=[dataclasses.asdict(ref)],
                        approved_by="daemon",
                        dry_run=False,
                        note="FAILED",
                    )
                )
        except stores_mod.StoreError as exc:
            notes.append(f"store {store_name} failed for {cand.path}: {exc}")
            continue

    try:
        journal_count = len(journal.entries())
    except OSError as exc:
        notes.append(f"journal recount failed: {exc}")
        journal_count = -1
    return {
        "ts": ts,
        "disk": disk,
        "candidates": len(top),
        "mirrored": mirrored,
        "journal_entries": journal_count,
        "notes": notes,
    }


def run_loop(config: config_mod.Config, interval_sec: int = 600, once: bool = False) -> dict | None:
    """Run :func:`run_once` forever (or once), printing a one-line report.

    Returns the last report when ``once`` is True, else never returns.
    """
    while True:
        report = run_once(config)
        print(
            f"[{report['ts']}] candidates={report['candidates']} "
            f"mirrored={len(report['mirrored'])} "
            f"journal_entries={report['journal_entries']} "
            f"notes={len(report['notes'])}",
            flush=True,
        )
        if once:
            return report
        time.sleep(interval_sec)
