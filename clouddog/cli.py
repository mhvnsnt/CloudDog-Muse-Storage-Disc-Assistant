"""CloudDog command-line interface.

Safety contract (v1):
- Every mutating command defaults to dry-run and only prints its plan.
- ``sweep --execute`` only MIRRORS (uploads) files to a permanent store.
  It never deletes anything; deletion is a future explicit ``reap`` command.
- Below the configured warn threshold, ``sweep`` runs an evaluation pass
  only: it lists candidates and takes no action, even with ``--execute``.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from datetime import datetime, timezone

from clouddog import config as config_mod
from clouddog import daemon as daemon_mod
from clouddog import stores as stores_mod
from clouddog.core import inventory, manifest, monitor, verifier
from clouddog.policy import approvals, laws


def _utcnow() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _human(num_bytes: int) -> str:
    """Format a byte count for humans."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def _load_config(path_arg: str | None) -> config_mod.Config:
    """Load config or exit 1 with the config error on stderr."""
    try:
        return config_mod.load_config(path_arg)
    except config_mod.ConfigError as exc:
        print(f"clouddog: config error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _journal(cfg: config_mod.Config) -> manifest.Journal:
    """Open the audit journal for *cfg*."""
    return manifest.Journal(cfg.journal_path)


def _route_store(kind: str, cfg: config_mod.Config) -> str | None:
    """Pick the store name for a candidate kind, or None if unroutable.

    Media-like and unknown files go to TeraBox when configured (bulk object
    store), falling back to GitHub. Repo-shaped things (git clones, venvs,
    bulk dirs) go to GitHub releases when configured, falling back to TeraBox.
    """
    names = set((cfg.stores or {}).keys())
    if kind in ("git-clone", "venv", "bulk-dir"):
        if "github" in names:
            return "github"
        if "terabox" in names:
            return "terabox"
        return None
    if "terabox" in names:
        return "terabox"
    if "github" in names:
        return "github"
    return None


def _remote_dir_for(store_name: str, cfg: config_mod.Config) -> str:
    """Default remote directory for *store_name*."""
    if store_name == "terabox":
        return cfg.terabox_remote_root or ""
    return ""


def _sweep_skip_reason(path: str) -> str:
    """Return a skip reason for *path*, or "" if it may be mirrored."""
    in_progress, reason = laws.is_in_progress(path)
    if in_progress:
        return f"in-progress ({reason}); NO-SWEEP-INFLIGHT"
    if laws.has_do_not_delete_marker(path):
        return "DO_NOT_DELETE marker on an ancestor directory"
    return ""


def _mirror_file(
    cfg: config_mod.Config,
    store_name: str,
    local_path: str,
    journal: manifest.Journal,
    ts: str,
    approved_by: str,
    remote_dir: str | None = None,
    remote_name: str | None = None,
) -> stores_mod.StoredRef:
    """Upload *local_path*, verify it, and journal the outcome.

    Raises :class:`stores_mod.VerificationFailed` (after journaling a FAILED
    "verify" entry) when the remote copy does not verify. Never journals a
    success that did not verify.
    """
    size = verifier.file_size(local_path)
    digest = verifier.sha256_file(local_path)
    store = stores_mod.get_store(store_name, cfg.stores or {})
    if remote_dir is None:
        remote_dir = _remote_dir_for(store_name, cfg)
    ref = stores_mod.run_with_retries(
        lambda: store.upload(local_path, remote_dir=remote_dir, remote_name=remote_name),
        what=f"upload {local_path} to {store_name}",
    )
    if not store.verify(ref):
        journal.append(
            manifest.JournalEntry(
                ts=ts,
                action="verify",
                path=local_path,
                size_bytes=size,
                sha256=digest,
                stores=[dataclasses.asdict(ref)],
                approved_by=approved_by,
                dry_run=False,
                note="FAILED",
            )
        )
        raise stores_mod.VerificationFailed(
            f"verify FAILED for {local_path} on {store_name}; success was NOT journaled"
        )
    journal.append(
        manifest.JournalEntry(
            ts=ts,
            action="store",
            path=local_path,
            size_bytes=size,
            sha256=digest,
            stores=[dataclasses.asdict(ref)],
            approved_by=approved_by,
            dry_run=False,
            note=f"mirrored to {store_name}",
        )
    )
    return ref


# ---------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    """Write a default config file (never overwrite without --force)."""
    target = os.path.expanduser(args.config or config_mod.DEFAULT_CONFIG_PATH)
    if os.path.exists(target) and not args.force:
        print(
            f"clouddog: config already exists at {target}; pass --force to overwrite it",
            file=sys.stderr,
        )
        return 1
    config_mod.write_default_config(target)
    print(f"wrote default config to {target}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print disk usage, journal summary, and configured stores (no network)."""
    cfg = _load_config(args.config)
    print("disk:")
    roots = ["/"] + [r for r in cfg.scan_roots if r != "/"]
    for root in roots:
        try:
            ds = monitor.disk_usage(root)
        except OSError as exc:
            print(f"  {root}: unreadable ({exc})")
            continue
        print(
            f"  {ds.path}: {ds.pct_used:.1f}% used "
            f"({_human(ds.used_bytes)} of {_human(ds.total_bytes)})"
        )
    journal = _journal(cfg)
    entries = journal.entries()
    print(f"journal: {len(entries)} entries ({journal.path})")
    for entry in entries[-3:]:
        print(journal.render_markdown(entry))
    print("stores:")
    for name in cfg.stores or {}:
        state = "configured" if name in stores_mod.REGISTRY else "unknown store name"
        print(f"  {name}: {state}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Plan (default) or mirror (with --execute --yes) the largest candidates.

    v1 ``--execute`` performs STORE (upload) only. Nothing is ever deleted.
    """
    cfg = _load_config(args.config)
    execute = bool(args.execute)
    if execute:
        try:
            approvals.require_execute(dry_run=False, assume_yes=args.yes, what="sweep --execute")
        except approvals.ApprovalRequired as exc:
            print(f"clouddog: {exc}", file=sys.stderr)
            print(
                "clouddog: re-run with --execute --yes to mirror (or omit --execute for a dry run)",
                file=sys.stderr,
            )
            return 2

    disk = monitor.disk_usage("/")
    warn_pct = cfg.thresholds.get("warn_pct", 75)
    below_threshold = disk.pct_used < warn_pct
    if below_threshold:
        print(
            f"disk at {disk.pct_used:.1f}% is below warn threshold {warn_pct}%: "
            "evaluation pass only (no actions)"
        )
    else:
        print(f"disk at {disk.pct_used:.1f}% meets warn threshold {warn_pct}%")

    candidates: list[inventory.Candidate] = []
    for root in cfg.scan_roots:
        try:
            candidates.extend(inventory.scan(root))
        except OSError as exc:
            print(f"  scan failed for {root}: {exc}", file=sys.stderr)
    candidates.sort(key=lambda c: c.size_bytes, reverse=True)
    print(f"top candidates ({len(candidates)} found):")

    journal = _journal(cfg)
    ts = _utcnow()
    plan_lines: list[str] = []
    mirrored = 0
    # Only act when the operator asked for --execute AND disk is over warn.
    acting = execute and not below_threshold
    for cand in candidates[:25]:
        skip = _sweep_skip_reason(cand.path)
        if skip:
            print(f"  SKIP {cand.path} [{cand.kind}] ({_human(cand.size_bytes)}): {skip}")
            continue
        store_name = _route_store(cand.kind, cfg)
        if store_name is None:
            print(f"  SKIP {cand.path} [{cand.kind}]: no store configured for this kind")
            continue
        if not acting:
            line = (
                f"would mirror {cand.path} [{cand.kind}] "
                f"({_human(cand.size_bytes)}) -> {store_name}"
            )
            print(f"  PLAN {line}")
            plan_lines.append(line)
            continue
        if not os.path.isfile(cand.path):
            print(f"  SKIP {cand.path}: v1 mirrors files only (kind {cand.kind})")
            continue
        try:
            ref = _mirror_file(cfg, store_name, cand.path, journal, ts, "cli:sweep")
        except stores_mod.StoreError as exc:
            print(f"  FAIL {cand.path}: {exc}", file=sys.stderr)
            continue
        print(f"  MIRRORED {cand.path} -> {store_name}:{ref.remote_path}")
        mirrored += 1

    journal.append(
        manifest.JournalEntry(
            ts=ts,
            action="sweep-plan",
            path=",".join(cfg.scan_roots),
            size_bytes=sum(c.size_bytes for c in candidates),
            sha256="",
            stores=[],
            approved_by="cli",
            dry_run=not acting,
            note="; ".join(plan_lines[:10])
            or ("executed mirror pass" if acting else "evaluation pass only"),
        )
    )
    if not acting:
        print("dry run complete: no uploads performed.")
    else:
        print(f"sweep --execute complete: mirrored {mirrored} file(s).")
    print("v1 note: sweep mirrors only; it never deletes. Deletion is a future 'reap' step.")
    return 0


def cmd_store(args: argparse.Namespace) -> int:
    """Store one file in a permanent store (dry-run plan by default)."""
    cfg = _load_config(args.config)
    local = os.path.abspath(os.path.expanduser(args.path))
    if not os.path.isfile(local):
        print(f"clouddog: not a file: {local}", file=sys.stderr)
        return 1
    store_name = args.to
    if store_name not in stores_mod.REGISTRY:
        print(
            f"clouddog: unknown store {store_name!r}; known: {sorted(stores_mod.REGISTRY)}",
            file=sys.stderr,
        )
        return 1
    if store_name not in (cfg.stores or {}):
        print(f"clouddog: store {store_name!r} is not configured", file=sys.stderr)
        return 1

    size = verifier.file_size(local)
    digest = verifier.sha256_file(local)
    remote_dir = args.remote_dir
    if remote_dir is None:
        remote_dir = _remote_dir_for(store_name, cfg)
    remote_name = args.remote_name or os.path.basename(local)
    print(f"plan: store {local} ({_human(size)}, sha256 {digest})")
    print(f"  -> {store_name}:{remote_dir}/{remote_name}")
    if not args.execute:
        print("dry run: nothing uploaded. pass --execute --yes to perform.")
        return 0
    try:
        approvals.require_execute(
            dry_run=False, assume_yes=args.yes, what=f"store {local} to {store_name}"
        )
    except approvals.ApprovalRequired as exc:
        print(f"clouddog: {exc}", file=sys.stderr)
        print("clouddog: re-run with --execute --yes to perform the upload", file=sys.stderr)
        return 2

    journal = _journal(cfg)
    ref = _mirror_file(
        cfg,
        store_name,
        local,
        journal,
        _utcnow(),
        "cli:store",
        remote_dir=remote_dir,
        remote_name=remote_name,
    )
    print(f"stored + verified: {ref.uri}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-verify the stores listed on a journal entry, then journal the result."""
    cfg = _load_config(args.config)
    journal = _journal(cfg)
    entries = journal.entries()
    n = args.journal_entry
    if n < 1 or n > len(entries):
        print(
            f"clouddog: journal entry {n} out of range (1..{len(entries)})",
            file=sys.stderr,
        )
        return 1
    entry = entries[n - 1]
    print(f"re-verifying journal entry {n}: {entry.action} {entry.path}")
    results: list[tuple[str, bool]] = []
    for s in entry.stores:
        name = s.get("store", "?")
        try:
            ref = stores_mod.StoredRef(
                store=name,
                remote_path=s.get("remote_path", ""),
                size=int(s.get("size", 0)),
                sha256=s.get("sha256", ""),
                uri=s.get("uri", ""),
            )
            store = stores_mod.get_store(name, cfg.stores or {})
            ok = bool(store.verify(ref))
        except stores_mod.StoreError as exc:
            print(f"  {name}: ERROR {exc}")
            results.append((name, False))
            continue
        print(f"  {name}: {'OK' if ok else 'FAIL'}")
        results.append((name, ok))
    journal.append(
        manifest.JournalEntry(
            ts=_utcnow(),
            action="verify",
            path=entry.path,
            size_bytes=entry.size_bytes,
            sha256=entry.sha256,
            stores=entry.stores,
            approved_by="cli:verify",
            dry_run=False,
            note="; ".join(f"{name}={'OK' if ok else 'FAIL'}" for name, ok in results),
        )
    )
    return 0 if all(ok for _, ok in results) else 1


def cmd_watch(args: argparse.Namespace) -> int:
    """Run the daemon mirror pass once (or loop on --interval)."""
    cfg = _load_config(args.config)
    daemon_mod.run_loop(cfg, interval_sec=args.interval, once=args.once)
    return 0


# ---------------------------------------------------------------- parser


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help=f"path to clouddog.yaml (default: {config_mod.DEFAULT_CONFIG_PATH})",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CloudDog argument parser."""
    parser = argparse.ArgumentParser(
        prog="clouddog",
        description="Disk-as-cache storage assistant. Dry-run is the default.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a default config file")
    _add_config_arg(p_init)
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")
    p_init.set_defaults(func=cmd_init)

    p_status = sub.add_parser("status", help="disk usage, journal summary, stores")
    _add_config_arg(p_status)
    p_status.set_defaults(func=cmd_status)

    p_sweep = sub.add_parser(
        "sweep",
        help="plan (default) or mirror (--execute) the largest candidates; never deletes",
    )
    _add_config_arg(p_sweep)
    p_sweep.add_argument("--dry-run", action="store_true", help="print the plan only")
    p_sweep.add_argument("--execute", action="store_true", help="perform the mirror")
    p_sweep.add_argument("--yes", action="store_true", help="approve execution")
    p_sweep.add_argument(
        "--owner-approved",
        action="store_true",
        help="reserved for the future reap step; v1 sweep never deletes",
    )
    p_sweep.set_defaults(func=cmd_sweep)

    p_store = sub.add_parser("store", help="store one file in a permanent store")
    _add_config_arg(p_store)
    p_store.add_argument("path", help="local file to store")
    p_store.add_argument("--to", required=True, help="store name (terabox, github, drive)")
    p_store.add_argument("--remote-dir", default=None, help="remote directory")
    p_store.add_argument("--remote-name", default=None, help="remote file name")
    p_store.add_argument("--execute", action="store_true", help="perform the upload")
    p_store.add_argument("--yes", action="store_true", help="approve execution")
    p_store.set_defaults(func=cmd_store)

    p_verify = sub.add_parser("verify", help="re-verify a journal entry's stores")
    _add_config_arg(p_verify)
    p_verify.add_argument(
        "--journal-entry", type=int, required=True, help="1-based journal entry number"
    )
    p_verify.set_defaults(func=cmd_verify)

    p_watch = sub.add_parser("watch", help="run the daemon mirror pass")
    _add_config_arg(p_watch)
    p_watch.add_argument(
        "--interval", type=int, default=600, help="seconds between runs (default 600)"
    )
    p_watch.add_argument("--once", action="store_true", help="run a single pass and exit")
    p_watch.set_defaults(func=cmd_watch)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SystemExit as exc:
        return int(exc.code or 0)
    except (config_mod.ConfigError, stores_mod.StoreError) as exc:
        print(f"clouddog: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
