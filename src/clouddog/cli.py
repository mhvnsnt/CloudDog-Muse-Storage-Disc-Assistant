"""CloudDog command-line interface.

Subcommands: scan, store, reap, daemon, verify, dashboard, du, dedupe.

Global flags:
  --dry-run   no destructive action anywhere (store/reap/daemon all honor it)
  --json      machine-readable output where the subcommand supports it
  --journal   audit journal path (default ~/.clouddog/audit.jsonl)

Exit codes: 0 ok, 2 usage (argparse), 3 verification/policy/operational failure.

All sibling-module imports are lazy (inside handlers) so the CLI stays
importable even while sibling modules are still being built in parallel.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

from clouddog.errors import CloudDogError

DEFAULT_JOURNAL = os.path.expanduser("~/.clouddog/audit.jsonl")
TIERS = ("github", "terabox", "drive")

# CloudDog-managed open-source tool installs (pulled in 2026-10-17).
TOOLS_DIR = os.path.expanduser("~/workspace/tools")
MANAGED_TOOLS = {
    "dust": os.path.join(TOOLS_DIR, "dust", "dust"),       # disk visualization
    "fclones": os.path.join(TOOLS_DIR, "fclones", "fclones"),  # duplicate finder
    "rclone": os.path.join(TOOLS_DIR, "rclone", "rclone"),  # multi-cloud (drive backend)
}


def find_tool(name: str) -> str | None:
    """Resolve an external tool: managed install first, then PATH."""
    managed = MANAGED_TOOLS.get(name)
    if managed and os.path.isfile(managed) and os.access(managed, os.X_OK):
        return managed
    return shutil.which(name)


# ---------------------------------------------------------------- helpers

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_journal(path: str):
    """Build an AuditJournal, creating its parent dir. Lazy import."""
    from clouddog.audit import AuditJournal

    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    return AuditJournal(path)


def _journal(journal, kind: str, path: str, size: int, locator: str, note: str = "") -> None:
    """Record one audit event; never let journaling fail the command."""
    if journal is None:
        return
    try:
        from clouddog.audit import AuditEvent

        journal.record(
            AuditEvent(ts=_utcnow(), kind=kind, path=path, size=size,
                       locator=locator, note=note)
        )
    except Exception as exc:  # journaling must not break the operation
        print(f"[clouddog] warning: journal write failed: {exc}", file=sys.stderr)


def _load_backend(tier: str):
    """Instantiate the backend for a tier. Lazy imports; clear error if missing."""
    tier = tier.lower()
    try:
        if tier == "github":
            from clouddog.stores.github import GitHubReleaseBackend

            return GitHubReleaseBackend()
        if tier == "terabox":
            from clouddog.stores.terabox import TeraBoxBackend

            return TeraBoxBackend()
        if tier == "drive":
            from clouddog.stores.drive import DriveBackend

            return DriveBackend()
    except ImportError as exc:
        raise CloudDogError(
            f"backend '{tier}' is not available in this build ({exc})"
        ) from exc
    raise CloudDogError(f"unknown backend tier '{tier}' (expected one of {', '.join(TIERS)})")


def _report_dict(report) -> dict:
    if dataclasses.is_dataclass(report):
        return dataclasses.asdict(report)
    if isinstance(report, dict):
        return report
    return {"report": str(report)}


# ---------------------------------------------------------------- scan

def cmd_scan(args: argparse.Namespace) -> int:
    from clouddog import policy

    try:
        entries = policy.scan(args.path, max_entries=args.max)
    except Exception as exc:
        print(f"[clouddog] scan failed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(entries, indent=2))
        return 0

    rows = [
        (
            str(e.get("path", "")),
            str(e.get("bytes", "")),
            str(e.get("action", "")),
            str(e.get("tier", "")),
            str(e.get("reason", "")),
        )
        for e in entries
    ]
    widths = [60, 12, 16, 10]
    header = (
        f"{'PATH':<{widths[0]}} {'BYTES':>{widths[1]}} "
        f"{'ACTION':<{widths[2]}} {'TIER':<{widths[3]}} REASON"
    )
    print(header)
    print("-" * len(header))
    for path, size, action, tier, reason in rows:
        if len(path) > widths[0]:
            path = "..." + path[-(widths[0] - 3):]
        print(f"{path:<{widths[0]}} {size:>{widths[1]}} {action:<{widths[2]}} {tier:<{widths[3]}} {reason}")
    print(f"\n{len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")
    return 0


# ---------------------------------------------------------------- store

def cmd_store(args: argparse.Namespace) -> int:
    from clouddog.stores.base import Health

    try:
        backend = _load_backend(args.tier)
    except CloudDogError as exc:
        print(f"[clouddog] {exc}", file=sys.stderr)
        return 3

    journal = _get_journal(args.journal)

    if not args.dry_run:
        try:
            hs = backend.health()
        except Exception as exc:
            print(f"[clouddog] health probe failed for backend '{args.tier}': {exc}",
                  file=sys.stderr)
            return 3
        if hs.health is not Health.LIVE:
            print(
                f"[clouddog] refusing: backend '{args.tier}' is not LIVE "
                f"(health={hs.health.value}: {hs.reason}). "
                f"Re-run with --dry-run to preview, or fix the backend.",
                file=sys.stderr,
            )
            _journal(journal, "error", args.path, 0, args.remote_ref,
                     f"store refused: backend '{args.tier}' health={hs.health.value}")
            return 3

    try:
        stored = backend.store(args.path, args.remote_ref, dry_run=args.dry_run)
    except CloudDogError as exc:
        print(f"[clouddog] store failed: {exc}", file=sys.stderr)
        _journal(journal, "error", args.path, 0, args.remote_ref, f"store failed: {exc}")
        return 3
    except Exception as exc:
        print(f"[clouddog] store crashed: {exc}", file=sys.stderr)
        _journal(journal, "error", args.path, 0, args.remote_ref, f"store crashed: {exc}")
        return 3

    print(json.dumps(stored.to_dict(), indent=2))
    _journal(journal, "stored", args.path, stored.size, stored.locator,
             f"backend={args.tier} dry_run={args.dry_run} cli=store")
    return 0


# ---------------------------------------------------------------- reap

def cmd_reap(args: argparse.Namespace) -> int:
    from clouddog.reaper import Reaper

    try:
        with open(args.manifest, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except Exception as exc:
        print(f"[clouddog] cannot read manifest {args.manifest}: {exc}", file=sys.stderr)
        return 3
    if not isinstance(manifest, dict):
        print(f"[clouddog] manifest {args.manifest} must be a JSON object "
              f'{{"rel": "sha"}}, got {type(manifest).__name__}', file=sys.stderr)
        return 2

    log_path = args.log_path or (os.path.splitext(args.manifest)[0] + ".log")
    journal = _get_journal(args.journal)

    # Live-store proof (C1): a live reap requires a backend + remote ref so
    # every delete is verified against the LIVE store, not just the manifest.
    # TeraBox is refused: its API exposes no remote checksum (H2).
    store_verify = None
    if not args.dry_run:
        backend_name = getattr(args, "backend", None)
        remote_ref = getattr(args, "remote_ref", None)
        if not backend_name or not remote_ref:
            print("[clouddog] live reap requires --backend and --remote-ref "
                  "(the manifest alone is a claim, not proof)", file=sys.stderr)
            return 2
        if backend_name == "terabox":
            print("[clouddog] refusing: terabox has no remote checksum — "
                  "cannot byte-verify before delete", file=sys.stderr)
            return 3
        try:
            backend = _backend_for(backend_name)
        except CloudDogError as exc:
            print(f"[clouddog] {exc}", file=sys.stderr)
            return 3
        from clouddog.stores.base import StoredRef
        if backend_name == "github":
            def store_verify(rel, full, _b=backend, _r=remote_ref):
                ref = StoredRef(backend="github",
                                locator=f"{_r}/{os.path.basename(full)}",
                                size=os.path.getsize(full), sha256="")
                return _b.verify(full, ref)
        else:  # drive: rclone path check against the remote tree
            def store_verify(rel, full, _b=backend, _r=remote_ref):
                ref = StoredRef(backend="drive", locator=f"{_r}/{rel}",
                                size=os.path.getsize(full), sha256="")
                return _b.verify(full, ref)

    try:
        reaper = Reaper(args.src_dir, manifest, log_path,
                        hash_algo=args.hash_algo, store_verify=store_verify)
        report = reaper.reap_once(dry_run=args.dry_run)
    except CloudDogError as exc:
        print(f"[clouddog] reap failed: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[clouddog] reap crashed: {exc}", file=sys.stderr)
        return 3

    rep = _report_dict(report)
    reaped_n = int(rep.get("reaped") or 0)
    if reaped_n:
        _journal(journal, "reaped", args.src_dir, int(rep.get("freed_bytes") or 0),
                 f"manifest={args.manifest}",
                 f"{reaped_n} files reaped dry_run={args.dry_run}")

    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"reaped:      {reaped_n}")
        print(f"mismatches:  {int(rep.get('mismatches') or 0)}")
        print(f"skipped:     {int(rep.get('skipped') or 0)}")
        print(f"freed_bytes: {int(rep.get('freed_bytes') or 0)}")
        print(f"halted:      {bool(rep.get('halted'))}")
        if rep.get("halted"):
            print(f"halt_reason: {rep.get('halt_reason', '')}")

    if rep.get("halted") or rep.get("mismatches"):
        return 3
    return 0


# ---------------------------------------------------------------- daemon

def cmd_daemon(args: argparse.Namespace) -> int:
    from clouddog.daemon import Daemon

    journal = _get_journal(args.journal)
    roots = (
        [r.strip() for r in args.roots.split(",") if r.strip()]
        if args.roots
        else ["/home/hatch"]
    )
    daemon = Daemon(roots=roots, interval_s=args.interval, journal=journal,
                    dry_run=args.dry_run)

    if args.once:
        try:
            report = daemon.tick()
        except CloudDogError as exc:
            print(f"[clouddog] daemon tick failed: {exc}", file=sys.stderr)
            return 3
        rep = dataclasses.asdict(report)
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            print(f"started_utc:   {rep['started_utc']}")
            print(f"disk:          {rep['disk_before_pct']:.1f}% -> {rep['disk_after_pct']:.1f}%")
            print(f"stored:        {rep['stored']}")
            print(f"verified:      {rep['verified']}")
            print(f"cleared:       {rep['cleared']}")
            print(f"freed_bytes:   {rep['freed_bytes']}")
            print(f"skipped_sacred:{rep['skipped_sacred']}")
            print(f"errors:        {len(rep['errors'])}")
            print(f"critical:      {rep['critical']}")
            if rep["critical"]:
                print(f"critical_msg:  {rep['critical_msg']}")
            for err in rep["errors"]:
                print(f"  error: {err}")
        return 0

    daemon.run_forever()
    return 0


# ---------------------------------------------------------------- verify

def cmd_verify(args: argparse.Namespace) -> int:
    from clouddog.stores.base import StoredRef

    try:
        backend = _load_backend(args.backend)
    except CloudDogError as exc:
        print(f"[clouddog] {exc}", file=sys.stderr)
        return 3

    ref = StoredRef(backend=args.backend, locator=args.locator,
                    size=args.size or 0, sha256=args.sha256 or "", extra={})
    try:
        ok = backend.verify(args.path, ref)
    except CloudDogError as exc:
        print(f"[clouddog] verify failed: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[clouddog] verify crashed: {exc}", file=sys.stderr)
        return 3

    out = {"match": bool(ok), "path": args.path, "locator": args.locator}
    print(json.dumps(out, indent=2))
    return 0 if ok else 3


# ---------------------------------------------------------------- dashboard

def cmd_dashboard(args: argparse.Namespace) -> int:
    # Lazy import: the dashboard module is built in parallel; the CLI must
    # work without it.
    try:
        from clouddog.dashboard.server import serve
    except ImportError as exc:
        print("[clouddog] dashboard module (clouddog.dashboard.server) is not "
              f"available in this build: {exc}", file=sys.stderr)
        return 3
    serve(args.port)
    return 0


# ---------------------------------------------------------------- external tools


def cmd_du(args: argparse.Namespace) -> int:
    """Visual disk usage via `dust` (pulled in 2026-10-17). Read-only."""
    exe = find_tool("dust")
    if exe is None:
        print("[clouddog] dust not found. Install: see README 'Open-source "
              "tooling' section.", file=sys.stderr)
        return 3
    cmd = [exe, "-d", str(args.depth), args.path]
    try:
        proc = subprocess.run(cmd, timeout=300)
    except subprocess.TimeoutExpired:
        print("[clouddog] dust timed out", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"[clouddog] dust failed: {exc}", file=sys.stderr)
        return 3
    return proc.returncode


def cmd_dedupe(args: argparse.Namespace) -> int:
    """Find duplicate files via `fclones` (pulled in 2026-10-17).

    REPORT ONLY — never deletes. Duplicate groups are candidates for the
    normal store -> verify -> clear flow.
    """
    exe = find_tool("fclones")
    if exe is None:
        print("[clouddog] fclones not found. Install: see README "
              "'Open-source tooling' section.", file=sys.stderr)
        return 3
    cmd = [exe, "group", "--format", "json", "--min", args.min_size, args.path]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        print("[clouddog] fclones timed out", file=sys.stderr)
        return 3
    except OSError as exc:
        print(f"[clouddog] fclones failed: {exc}", file=sys.stderr)
        return 3
    if proc.returncode != 0:
        print(f"[clouddog] fclones error: {proc.stderr.strip()[:300]}",
              file=sys.stderr)
        return 3
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        print("[clouddog] fclones returned unparseable JSON", file=sys.stderr)
        return 3
    groups = []
    wasted = 0
    for g in data.get("groups", []):
        raw_files = g.get("files", []) or []
        files = [f if isinstance(f, str) else f.get("path")
                 for f in raw_files]
        files = [f for f in files if f]
        size = int(g.get("file_len", 0) or 0)
        if len(files) > 1 and size > 0:
            groups.append({"size": size, "count": len(files), "files": files})
            wasted += size * (len(files) - 1)
    groups.sort(key=lambda g: g["size"] * g["count"], reverse=True)
    if args.json:
        print(json.dumps({"groups": groups, "wasted_bytes": wasted,
                          "path": args.path}, indent=2))
    else:
        print(f"[clouddog] {len(groups)} duplicate groups, "
              f"~{wasted / 1e9:.2f} GB reclaimable (report only, nothing deleted)")
        for g in groups[:20]:
            print(f"  {g['size'] / 1e6:.1f} MB x{g['count']}: {g['files'][0]}")
            for f in g["files"][1:4]:
                print(f"      = {f}")
    _journal(_get_journal(args.journal), "skipped", args.path, wasted,
             "dedupe-report", f"{len(groups)} duplicate groups found")
    return 0


# ---------------------------------------------------------------- parser / entry

def _add_common_flags(sp: argparse.ArgumentParser) -> None:
    """Repeat the global flags on a subparser so they work in either position
    (``clouddog --json scan ...`` and ``clouddog scan --json ...``).

    The copies use ``default=argparse.SUPPRESS`` because argparse parses each
    subcommand into a FRESH namespace and copies every key back over the
    parent namespace -- a real default here would clobber a value the user
    already set in global position. With SUPPRESS, the subparser only
    contributes the flag when it was actually passed there.
    """
    sp.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                    help="no destructive action anywhere (default)")
    sp.add_argument("--apply", action="store_false", dest="dry_run",
                    default=argparse.SUPPRESS,
                    help="actually perform destructive actions")
    sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                    help="machine-readable output")
    sp.add_argument("--journal", default=argparse.SUPPRESS,
                    help="audit journal path")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="clouddog",
        description="CloudDog v1: mirror everything to GitHub/TeraBox/Drive, "
                    "verify byte-exact, then clear local.",
    )
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="no destructive action anywhere (default)")
    p.add_argument("--apply", action="store_false", dest="dry_run",
                   help="actually perform destructive actions (overrides --dry-run)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.add_argument("--journal", default=DEFAULT_JOURNAL,
                   help="audit journal path (default %(default)s)")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    s = sub.add_parser("scan", help="print the policy scan for a path")
    _add_common_flags(s)
    s.add_argument("--path", required=True, help="root path to scan")
    s.add_argument("--max", type=int, default=200,
                   help="max entries to report (default 200)")
    s.set_defaults(func=cmd_scan)

    st = sub.add_parser("store", help="store a file/dir to a backend and journal it")
    _add_common_flags(st)
    st.add_argument("path", help="local file or directory to store")
    st.add_argument("--tier", required=True, choices=list(TIERS),
                    help="backend tier")
    st.add_argument("--remote-ref", required=True,
                    help="backend remote ref (github: owner/repo@tag, "
                         "terabox: /remote/dir, drive: remote:path)")
    st.set_defaults(func=cmd_store)

    r = sub.add_parser("reap", help="run one delete-as-you-go reaper pass")
    _add_common_flags(r)
    r.add_argument("--manifest", required=True,
                   help='path to {"rel": "sha"} JSON manifest')
    r.add_argument("--src-dir", required=True, help="source directory to reap")
    r.add_argument("--hash-algo", default="git-sha1", choices=["git-sha1", "sha256"])
    r.add_argument("--log-path", default=None, help="reaper log path")
    r.add_argument("--backend", choices=["github", "terabox", "drive"],
                   default=None,
                   help="store tier for LIVE verification (required for live reap)")
    r.add_argument("--remote-ref", default=None,
                   help="remote ref for verification, e.g. 'owner/repo@tag' "
                        "(github) or 'remote:/path' (drive); required for live reap")
    r.set_defaults(func=cmd_reap)

    d = sub.add_parser("daemon", help="run the hygiene daemon")
    _add_common_flags(d)
    d.add_argument("--interval", type=int, default=600,
                   help="seconds between ticks (default 600)")
    d.add_argument("--once", action="store_true",
                   help="single tick then exit")
    d.add_argument("--roots", default=None,
                   help="comma-separated roots to sweep (default /home/hatch)")
    d.set_defaults(func=cmd_daemon)

    v = sub.add_parser("verify", help="verify a local path against a stored ref")
    _add_common_flags(v)
    v.add_argument("path", help="local file to verify")
    v.add_argument("--backend", required=True, choices=list(TIERS))
    v.add_argument("--locator", required=True, help="backend locator of the stored object")
    v.add_argument("--size", type=int, default=0, help="expected size in bytes")
    v.add_argument("--sha256", default="", help="expected sha256 hex")
    v.set_defaults(func=cmd_verify)

    db = sub.add_parser("dashboard", help="serve the CloudDog dashboard")
    _add_common_flags(db)
    db.add_argument("--port", type=int, default=8899)
    db.set_defaults(func=cmd_dashboard)

    du = sub.add_parser("du", help="visual disk usage via dust (read-only)")
    _add_common_flags(du)
    du.add_argument("--path", required=True, help="root path to visualize")
    du.add_argument("--depth", type=int, default=1,
                    help="tree depth (default 1)")
    du.set_defaults(func=cmd_du)

    dd = sub.add_parser("dedupe",
                        help="find duplicate files via fclones (report only)")
    _add_common_flags(dd)
    dd.add_argument("--path", required=True, help="root path to scan")
    dd.add_argument("--min-size", default="1M",
                    help="minimum file size to consider (default 1M)")
    dd.set_defaults(func=cmd_dedupe)

    return p


def main(argv=None) -> int:
    """CLI entry point. Returns a process exit code (0/2/3)."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except CloudDogError as exc:
        print(f"[clouddog] error: {exc}", file=sys.stderr)
        return 3
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
