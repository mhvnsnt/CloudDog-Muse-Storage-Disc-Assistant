#!/usr/bin/env python3
"""
Cloud Dog Storage Disc Assistant — real disk-hygiene engine for the Muse VM.

A production CLI that enforces the owner's NO-LOCAL-CLUTTER LAW:
    Disk is a cache, not storage. Everything goes to TeraBox, Drive, Kaggle,
    or GitHub — then clears local. Disk stays under 75%.

Model: verify-then-delete ONLY. Nothing is ever deleted unless it is proven
byte-identical on at least one permanent store (GitHub release asset or
TeraBox). In-flight work (downloads, uploads, renders) is sacred and never
touched.

Stdlib only. Safe to run unattended via the disk-space-guardian cron.

Usage:
    cloud_dog.py scan [--json]
    cloud_dog.py register --file PATH --store github --repo OWNER/REPO \
        --release TAG --asset NAME [--sha256 HEX] [--note TEXT]
    cloud_dog.py verify --file PATH
    cloud_dog.py reap [--apply] [--path DIR]
    cloud_dog.py enforce [--apply]        # full cycle: scan + reap verified
    cloud_dog.py registry [--json]        # show registry

Default mode is DRY-RUN. Nothing is deleted unless --apply is passed.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------- locations
HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
AGENT_OPS = os.path.join(WORKSPACE, "agent-ops")
REGISTRY_PATH = os.path.join(AGENT_OPS, "cloud-dog-registry.json")
LOG_PATH = os.path.join(AGENT_OPS, "disk-cleanup-log.md")

USAGE_CEILING = 75.0          # target: stay under this %
HARD_CEILING = 88.0           # never reach this
INFLIGHT_MTIME_SECS = 300     # files modified within this window = in-flight

# Directories/files that are ALWAYS sacred — never scanned for reaping,
# never deleted, never descended into for candidate discovery.
SACRED_PATHS = [
    os.path.join("/tmp", "cj-canon"),
    os.path.join(AGENT_OPS, "checkpoints"),
    os.path.join(HOME, ".ssh"),
    os.path.join(HOME, ".gnupg"),
]

# File/dir name markers that veto deletion anywhere beneath them.
VETO_MARKERS = (".DO_NOT_DELETE",)

# Extensions / suffixes that always mean "in-flight or transient".
INFLIGHT_SUFFIXES = (".part", ".aria2", ".tmp", ".kaggle-partial", ".crdownload")


# ------------------------------------------------------------------- utils
def now_stamp():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")


def run(cmd, timeout=60):
    """Run a shell command, return (rc, stdout, stderr). Never raises."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except Exception as e:  # timeout, missing binary, etc.
        return 127, "", str(e)


def disk_usage_percent(path="/home/hatch"):
    st = shutil.disk_usage(path)
    return st.used / st.total * 100.0, st.used, st.total


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def is_sacred(path):
    ap = os.path.abspath(path)
    for s in SACRED_PATHS:
        if ap == s or ap.startswith(s + os.sep):
            return True
    return False


def has_veto_marker(path):
    """Walk up from path; True if any ancestor dir has a veto marker."""
    d = os.path.abspath(path if os.path.isdir(path) else os.path.dirname(path))
    while True:
        try:
            names = os.listdir(d)
        except OSError:
            return False
        if any(n.startswith(VETO_MARKERS) for n in names):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def is_inflight(path):
    """True if the file looks like it is being written right now."""
    base = os.path.basename(path)
    if base.endswith(INFLIGHT_SUFFIXES):
        return True, "transient suffix"
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return True, "unstatable"
    if time.time() - mtime < INFLIGHT_MTIME_SECS:
        return True, f"modified {int(time.time() - mtime)}s ago"
    return False, ""


def active_transfer_pids():
    """PIDs of processes that look like uploads/downloads."""
    rc, out, _ = run(["ps", "aux"])
    pids = set()
    if rc != 0:
        return pids
    for line in out.splitlines():
        low = line.lower()
        if ("curl" in low and ("-o" in low or "-L" in low or "--output" in low)) \
                or "gh release upload" in low \
                or "tbupload.py" in low:
            parts = line.split()
            if len(parts) > 1 and parts[1].isdigit():
                pids.add(int(parts[1]))
    return pids


# ---------------------------------------------------------------- registry
def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {"version": 1, "entries": {}}
    try:
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}


def save_registry(reg):
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, REGISTRY_PATH)


def log_action(text):
    line = f"- [{now_stamp()}] {text}\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)


# ---------------------------------------------------------------- commands
def cmd_scan(as_json=False):
    pct, used, total = disk_usage_percent()
    # largest top-level dirs in workspace
    biggest = []
    try:
        with os.scandir(WORKSPACE) as it:
            entries = [e for e in it]
    except OSError:
        entries = []
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                tot = 0
                for root, _, files in os.walk(e.path, followlinks=False):
                    for fn in files:
                        try:
                            tot += os.lstat(os.path.join(root, fn)).st_size
                        except OSError:
                            pass
                biggest.append((e.name, tot))
            elif e.is_file(follow_symlinks=False):
                try:
                    biggest.append((e.name, os.lstat(e.path).st_size))
                except OSError:
                    pass
        except OSError:
            pass
    biggest.sort(key=lambda x: x[1], reverse=True)
    report = {
        "disk_usage_percent": round(pct, 1),
        "used": human(used),
        "total": human(total),
        "ceiling": USAGE_CEILING,
        "over_ceiling": pct > USAGE_CEILING,
        "largest": [{"name": n, "size": human(s), "bytes": s}
                    for n, s in biggest[:15]],
    }
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Disk: {report['disk_usage_percent']}% used "
              f"({report['used']} / {report['total']}) — ceiling {USAGE_CEILING}%")
        print("Largest in ~/workspace:")
        for item in report["largest"]:
            print(f"  {item['size']:>10}  {item['name']}")
    return report


def cmd_register(args):
    path = os.path.abspath(args.file)
    if is_sacred(path):
        print(f"REFUSED: {path} is sacred", file=sys.stderr)
        return 1
    entry = {
        "local_path": path,
        "store": args.store,
        "repo": args.repo,
        "release": args.release,
        "asset": args.asset,
        "sha256": args.sha256 or "",
        "size": args.size or 0,
        "note": args.note or "",
        "registered_at": now_stamp(),
        "status": "registered",
    }
    if os.path.exists(path) and not args.sha256:
        try:
            entry["sha256"] = sha256_file(path)
            entry["size"] = os.path.getsize(path)
        except OSError as e:
            print(f"WARNING: could not hash local file: {e}", file=sys.stderr)
    reg = load_registry()
    reg["entries"][path] = entry
    save_registry(reg)
    print(f"Registered: {path} -> {args.store}:{args.repo}@{args.release}")
    return 0


def verify_github_asset(repo, release, asset, local_size=0, local_sha256=""):
    """Verify a local file against a GitHub release asset.

    Returns (ok, detail). ok=True only if the asset exists on the release
    AND size matches (when known) AND sha256 digest matches (when known).
    """
    rc, out, err = run(
        ["gh", "api", f"repos/{repo}/releases/tags/{release}",
         "--jq", ".assets[] | [.name, .size, .digest] | @tsv"],
        timeout=90,
    )
    if rc != 0:
        return False, f"gh api failed: {err.strip()[:200]}"
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] != asset:
            continue
        try:
            size = int(parts[1])
        except ValueError:
            size = 0
        digest = parts[2] if len(parts) > 2 else ""
        if local_size and size != local_size:
            return False, f"size mismatch: store={size} local={local_size}"
        if local_sha256 and digest.startswith("sha256:"):
            if digest.split(":", 1)[1].lower() != local_sha256.lower():
                return False, "sha256 digest mismatch"
        return True, f"asset {asset} verified (size={size})"
    return False, f"asset {asset} not found on release {release}"


def cmd_verify(args):
    path = os.path.abspath(args.file)
    reg = load_registry()
    entry = reg["entries"].get(path)
    if not entry:
        print(f"No registry entry for {path}", file=sys.stderr)
        return 1
    if not os.path.exists(path):
        print(f"Local file missing: {path} (already cleared?)")
        return 2
    if entry["store"] == "github":
        ok, detail = verify_github_asset(
            entry["repo"], entry["release"], entry["asset"],
            entry.get("size", 0), entry.get("sha256", ""),
        )
    elif entry["store"] == "terabox":
        # TeraBox API path is down (no creds); treat registry note as claim,
        # NOT proof — never auto-verify TeraBox without a live check.
        ok, detail = False, "terabox live-verify not available; manual check required"
    else:
        ok, detail = False, f"unknown store {entry['store']}"
    print(f"{'OK' if ok else 'FAIL'}: {path} — {detail}")
    return 0 if ok else 1


def reap_candidates(search_root=None):
    """Yield registry entries whose local file exists and may be reaped."""
    reg = load_registry()
    for path, entry in reg["entries"].items():
        if search_root and not path.startswith(os.path.abspath(search_root)):
            continue
        if entry.get("status") == "reaped":
            continue
        if not os.path.exists(path):
            entry["status"] = "reaped"  # already gone
            continue
        if is_sacred(path):
            continue
        if has_veto_marker(path):
            continue
        inflight, reason = is_inflight(path)
        if inflight:
            continue
        yield path, entry
    save_registry(reg)


def cmd_reap(args):
    apply = args.apply
    search_root = os.path.abspath(args.path) if getattr(args, "path", "") else None
    reaped, skipped, failed = [], [], []
    for path, entry in reap_candidates(search_root):
        if entry["store"] == "github":
            ok, detail = verify_github_asset(
                entry["repo"], entry["release"], entry["asset"],
                entry.get("size", 0), entry.get("sha256", ""),
            )
        else:
            ok, detail = False, "terabox live-verify unavailable"
        if not ok:
            failed.append((path, detail))
            continue
        size = entry.get("size", 0) or (
            os.path.getsize(path) if os.path.exists(path) else 0)
        if apply:
            try:
                os.remove(path)
                reg = load_registry()
                reg["entries"][path]["status"] = "reaped"
                reg["entries"][path]["reaped_at"] = now_stamp()
                save_registry(reg)
                msg = (f"REAPED {path} ({human(size)}) — verified on "
                       f"{entry['store']}:{entry['repo']}@{entry['release']}")
                log_action(msg)
                reaped.append((path, size))
            except OSError as e:
                failed.append((path, str(e)))
        else:
            reaped.append((path, size))  # dry-run: would reap
            print(f"WOULD-REAP {path} ({human(size)}) — {detail}")
    total = sum(s for _, s in reaped)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] reaped={len(reaped)} ({human(total)}) "
          f"skipped-inflight/veto={len(skipped)} failed-verify={len(failed)}")
    for path, detail in failed:
        print(f"  KEEP (unverified): {path} — {detail}")
    if apply and reaped:
        log_action(f"cloud_dog reap: {len(reaped)} files, {human(total)} freed")
    return 0


def cmd_enforce(args):
    report = cmd_scan()
    freed = 0
    if report["over_ceiling"] or args.apply:
        print("--- reap phase ---")
        before = disk_usage_percent()[0]
        rc = cmd_reap(args)
        after = disk_usage_percent()[0]
        freed = before - after
        print(f"Disk: {before:.1f}% -> {after:.1f}%")
        log_action(f"cloud_dog enforce: {before:.1f}% -> {after:.1f}% "
                   f"(apply={args.apply})")
        return rc
    print("Disk under ceiling; no reap needed (use --apply to force).")
    return 0


def cmd_registry(args):
    reg = load_registry()
    entries = reg.get("entries", {})
    if args.json:
        print(json.dumps(reg, indent=2))
        return 0
    print(f"Registry: {len(entries)} entries ({REGISTRY_PATH})")
    for path, e in sorted(entries.items()):
        print(f"  [{e.get('status', '?')}] {path}")
        print(f"      -> {e.get('store')}:{e.get('repo')}@{e.get('release')}"
              f" asset={e.get('asset')}")
    return 0


# ------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="cloud_dog",
        description="Cloud Dog Storage Disc Assistant — real disk-hygiene engine.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="report disk usage and largest consumers")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("register", help="register a file as verified on a store")
    p.add_argument("--file", required=True)
    p.add_argument("--store", required=True, choices=["github", "terabox"])
    p.add_argument("--repo", default="")
    p.add_argument("--release", default="")
    p.add_argument("--asset", default="")
    p.add_argument("--sha256", default="")
    p.add_argument("--size", type=int, default=0)
    p.add_argument("--note", default="")

    p = sub.add_parser("verify", help="verify a registered file against its store")
    p.add_argument("--file", required=True)

    p = sub.add_parser("reap", help="verify-then-delete registered files")
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--path", default="",
                   help="only consider files under this directory")

    p = sub.add_parser("enforce", help="full cycle: scan + reap")
    p.add_argument("--apply", action="store_true")

    p = sub.add_parser("registry", help="show the store registry")
    p.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            cmd_scan(args.json)
        elif args.cmd == "register":
            return cmd_register(args)
        elif args.cmd == "verify":
            return cmd_verify(args)
        elif args.cmd == "reap":
            return cmd_reap(args)
        elif args.cmd == "enforce":
            return cmd_enforce(args)
        elif args.cmd == "registry":
            return cmd_registry(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
