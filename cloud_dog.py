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
    cloud_dog.py register --file PATH --store github|terabox|kaggle \
        --repo OWNER/REPO --release TAG --asset NAME \
        [--remote-dir /tb/dir] [--dataset owner/slug] \
        [--sha256 HEX] [--note TEXT]
    cloud_dog.py verify --file PATH
    cloud_dog.py reap [--apply] [--path DIR]
    cloud_dog.py enforce [--apply]        # full cycle: scan + reap verified
    cloud_dog.py registry [--json]        # show registry
    cloud_dog.py mirror --magnet URI --store github --repo O/R --release T
        [--part-mb 100]                   # torrent -> store, bounded disk
    cloud_dog.py daemon [--interval 600] [--apply]
        # continuous enforce loop (pidfile, SIGTERM-clean)

v2 (2026-10-17): terabox backend (tbupload.py), kaggle backend (REST),
torrent mirror mode (TorBox cloud -> Range-stream -> part upload/verify/
delete), daemon mode, structured audit journal (cloud-dog-audit.jsonl).

v3 (2026-10-17): sqlite-backed registry (Python stdlib sqlite3, WAL mode)
replaces the JSON file — atomic transactions, concurrent-safe for the
guardian cron + manual runs. Legacy cloud-dog-registry.json migrates
once on first open; `registry_export_json()` dumps the legacy shape.

Default mode is DRY-RUN. Nothing is deleted unless --apply is passed.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------- locations
HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
AGENT_OPS = os.path.join(WORKSPACE, "agent-ops")
REGISTRY_PATH = os.path.join(AGENT_OPS, "cloud-dog-registry.json")
REGISTRY_DB_PATH = os.path.join(AGENT_OPS, "cloud-dog-registry.db")
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
    target = path if os.path.exists(path) else (HOME if os.path.exists(HOME) else "/")
    st = shutil.disk_usage(target)
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
    if "agent-ops/checkpoints" in ap or ("agent-ops" + os.sep + "checkpoints") in ap:
        return True
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
#
# sqlite-backed registry (v3). The JSON file at REGISTRY_PATH is legacy:
# on first DB open its entries are migrated once into sqlite, then sqlite
# is the single source of truth. sqlite gives us atomic transactions and
# concurrent-safe access (WAL mode) — the old load-modify-save JSON pattern
# raced when the guardian cron and a manual run overlapped.
#
# load_registry() / save_registry() keep their exact v1/v2 API so every
# call site works unchanged.

_REGISTRY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS entries ("
    " local_path TEXT PRIMARY KEY,"
    " data TEXT NOT NULL,"
    " updated_at TEXT NOT NULL)"
)
_REGISTRY_META_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS meta ("
    " k TEXT PRIMARY KEY, v TEXT NOT NULL)"
)


def _db():
    """Open the registry DB, creating schema and migrating legacy JSON once."""
    os.makedirs(os.path.dirname(REGISTRY_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(REGISTRY_DB_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(_REGISTRY_SCHEMA)
        conn.execute(_REGISTRY_META_SCHEMA)
        row = conn.execute(
            "SELECT v FROM meta WHERE k='json_migrated'").fetchone()
        if not row and os.path.exists(REGISTRY_PATH):
            try:
                with open(REGISTRY_PATH) as f:
                    legacy = json.load(f)
                entries = (legacy.get("entries", {})
                           if isinstance(legacy, dict) else {})
                stamp = now_stamp()
                for p, e in entries.items():
                    conn.execute(
                        "INSERT OR REPLACE INTO entries"
                        " (local_path, data, updated_at) VALUES (?, ?, ?)",
                        (p, json.dumps(e), stamp))
                conn.execute(
                    "INSERT INTO meta (k, v) VALUES ('json_migrated', '1')")
                conn.commit()
            except (OSError, json.JSONDecodeError):
                conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def load_registry():
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT local_path, data FROM entries").fetchall()
        entries = {}
        for p, d in rows:
            try:
                entries[p] = json.loads(d)
            except (json.JSONDecodeError, TypeError):
                continue
        return {"version": 3, "entries": entries}
    finally:
        conn.close()


def save_registry(reg):
    conn = _db()
    try:
        stamp = now_stamp()
        entries = reg.get("entries", {})
        with conn:  # atomic transaction
            conn.execute("DELETE FROM entries")
            for p, e in entries.items():
                conn.execute(
                    "INSERT INTO entries (local_path, data, updated_at)"
                    " VALUES (?, ?, ?)",
                    (p, json.dumps(e), stamp))
    finally:
        conn.close()


def registry_export_json(dest_path=None):
    """Dump the sqlite registry back to the legacy JSON shape (backup/audit)."""
    reg = load_registry()
    dest = dest_path or (REGISTRY_PATH + ".export")
    tmp = dest + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"version": 3, "entries": reg["entries"]},
                  f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dest)
    return dest


def log_action(text):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
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
        "remote_dir": getattr(args, "remote_dir", "") or "",
        "dataset": getattr(args, "dataset", "") or "",
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
    audit_log("register", path=path, store=args.store,
              repo=args.repo, release=args.release, asset=args.asset,
              remote_dir=getattr(args, "remote_dir", "") or "",
              dataset=getattr(args, "dataset", "") or "",
              sha256=entry["sha256"][:16], size=entry["size"])
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
    print(f"Registry: {len(entries)} entries ({REGISTRY_DB_PATH})")
    for path, e in sorted(entries.items()):
        print(f"  [{e.get('status', '?')}] {path}")
        print(f"      -> {e.get('store')}:{e.get('repo')}@{e.get('release')}"
              f" asset={e.get('asset')}")
    return 0


#!/usr/bin/env python3
"""
CloudDog v2 additions — store backends (terabox, kaggle), torrent mirror mode,
daemon mode, structured audit journal.

All additive to v1. v1 verify-then-delete semantics are unchanged:
nothing is ever deleted unless proven byte-identical on a live store.

Stdlib only. No new dependencies.
"""

# ================================================================= v2: paths
TBUpload_PY = os.path.join(WORKSPACE, "tools", "terabox-uploader", "tbupload.py")
TBUpload_CREDS = os.path.join(WORKSPACE, "tools", "terabox-uploader", ".creds.json")
KAGGLE_JSON = os.path.join(HOME, ".kaggle", "kaggle.json")
AUDIT_PATH = os.path.join(AGENT_OPS, "cloud-dog-audit.jsonl")
MIRROR_STAGING = os.path.join(AGENT_OPS, "mirror-staging")
DAEMON_PIDFILE = os.path.join(AGENT_OPS, "cloud-dog-daemon.pid")

STORES = ("github", "terabox", "kaggle")

UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# TorBox cloud API (used by mirror mode; credential via authd surrogate).
TORBOX_API = "https://api.torbox.app/v1/api"


# ============================================================ v2: audit journal
def audit_log(action, **fields):
    """Append one structured JSON event to the audit journal.

    The human-readable disk-cleanup-log.md stays the owner-visible record;
    this JSONL file is the machine-readable twin. Never raises.
    """
    event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "action": action}
    for k in sorted(fields):
        event[k] = fields[k]
    try:
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError:
        pass
    return event


# ============================================================ v2: terabox backend
def terabox_creds_present():
    """True only if the API upload path was activated (see TERABOX-SESSION-KEEPER.md)."""
    return os.path.exists(TBUpload_PY) and os.path.exists(TBUpload_CREDS)


def terabox_upload(local_path, remote_dir, remote_name=None):
    """Upload one file via tbupload.py (4MB chunks, auto-verify).

    Returns (ok, detail). ok=True only if tbupload.py exits 0 AND prints
    its VERIFY OK line (it re-lists the remote dir itself).
    """
    if not terabox_creds_present():
        return False, ("terabox api creds not configured (.creds.json missing) "
                        "- use the browser upload lane; "
                        "see agent-ops/TERABOX-SESSION-KEEPER.md")
    cmd = [sys.executable, TBUpload_PY, "upload", local_path, remote_dir]
    if remote_name:
        cmd.append(remote_name)
    rc, out, err = run(cmd, timeout=7200)
    if rc == 0 and "VERIFY OK" in out:
        return True, "uploaded + verified via tbupload.py"
    tail = (out.strip().splitlines() or [err.strip() or f"exit {rc}"])[-1]
    return False, tail[:300]


def terabox_list(remote_dir):
    """List a TeraBox remote dir. Returns (ok, entries|detail).

    entries: [{'name': str, 'size': int, 'isdir': bool}].
    Parses `tbupload.py ls` lines: 'f <size> <name>' / 'd <size> <name>'.
    """
    if not terabox_creds_present():
        return False, "terabox api creds not configured (.creds.json missing)"
    rc, out, err = run([sys.executable, TBUpload_PY, "ls", remote_dir],
                       timeout=120)
    if rc != 0:
        tail = (out.strip().splitlines() or [err.strip() or f"exit {rc}"])[-1]
        return False, tail[:200]
    entries = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0] in ("f", "d"):
            try:
                entries.append({"isdir": parts[0] == "d",
                                "size": int(parts[1]),
                                "name": parts[2]})
            except ValueError:
                continue
    return True, entries


def verify_terabox_entry(entry, local_path):
    """Verify a registry entry against TeraBox (live ls + size match).

    Registry entry needs: remote_dir, asset (remote filename).
    Never passes without a live listing - a registry claim alone is not proof.
    """
    remote_dir = entry.get("remote_dir", "")
    asset = entry.get("asset", "")
    if not remote_dir or not asset:
        return False, "terabox entry needs remote_dir + asset"
    ok, res = terabox_list(remote_dir)
    if not ok:
        return False, res
    for e in res:
        if not e["isdir"] and e["name"] == asset:
            want = entry.get("size", 0)
            if want and e["size"] != want:
                return False, (f"size mismatch: remote={e['size']} "
                                f"local={want}")
            return True, (f"terabox:{remote_dir}/{asset} verified "
                          f"({e['size']} bytes)")
    return False, f"{asset} not found in terabox:{remote_dir}"


# ============================================================ v2: kaggle backend
def kaggle_creds():
    """Return (username, key) from ~/.kaggle/kaggle.json, or (None, None)."""
    try:
        with open(KAGGLE_JSON) as f:
            c = json.load(f)
        if c.get("username") and c.get("key"):
            return c["username"], c["key"]
    except (OSError, ValueError):
        pass
    return None, None


def kaggle_request(method, path, data=None, headers=None, timeout=120):
    """Stdlib HTTPS call to the Kaggle REST API.

    Auth: HTTP Basic from ~/.kaggle/kaggle.json. data may be dict (sent as
    JSON) or bytes (sent raw, caller sets Content-Type).
    Returns (ok, parsed_json|detail).
    """
    import urllib.request
    import urllib.error
    import base64
    user, key = kaggle_creds()
    if not user:
        return False, "kaggle api creds not configured (~/.kaggle/kaggle.json missing)"
    url = "https://www.kaggle.com/api/v1" + path
    token = base64.b64encode(f"{user}:{key}".encode()).decode()
    h = {"Authorization": "Basic " + token,
         "User-Agent": UA_BROWSER,
         "Accept": "application/json"}
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body = bytes(data)
        else:
            body = json.dumps(data).encode()
            h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:200].decode("utf-8", "replace")
        return False, f"kaggle api HTTP {e.code}: {detail}"
    except Exception as e:
        return False, f"kaggle api error: {e}"
    try:
        return True, json.loads(raw.decode("utf-8"))
    except ValueError:
        return False, "kaggle api returned non-JSON"


def kaggle_dataset_view(owner_slug):
    """GET /datasets/view/{owner}/{slug}. Returns (ok, payload|detail)."""
    return kaggle_request("GET", f"/datasets/view/{owner_slug}")


def verify_kaggle_entry(entry, local_path):
    """Verify a registry entry against a Kaggle dataset (live view + size).

    Registry entry needs: dataset ("owner/slug"), asset (filename).
    """
    dataset = entry.get("dataset", "")
    asset = entry.get("asset", "")
    if not dataset or not asset:
        return False, "kaggle entry needs dataset (owner/slug) + asset (filename)"
    ok, payload = kaggle_dataset_view(dataset)
    if not ok:
        return False, payload
    files = payload.get("files", []) if isinstance(payload, dict) else []
    for f in files:
        name = f.get("name") or os.path.basename(f.get("ref", ""))
        ref = f.get("ref", "")
        if name == asset or ref.endswith("/" + asset):
            want = entry.get("size", 0)
            got = int(f.get("totalBytes") or f.get("size") or 0)
            if want and got and got != want:
                return False, f"size mismatch: remote={got} local={want}"
            return True, f"kaggle:{dataset} file {asset} verified ({got} bytes)"
    return False, f"{asset} not found in kaggle dataset {dataset}"


def multipart_encode(fields, files):
    """Hand-rolled multipart/form-data encoder (stdlib only).

    fields: {name: str_value}
    files: {name: (filename, bytes, content_type)}
    Returns (body_bytes, content_type_header).
    """
    import uuid
    boundary = uuid.uuid4().hex
    buf = bytearray()
    for k, v in fields.items():
        buf += (f'--{boundary}\r\nContent-Disposition: form-data; '
                f'name="{k}"\r\n\r\n{v}\r\n').encode()
    for k, (fname, data, ctype) in files.items():
        buf += (f'--{boundary}\r\nContent-Disposition: form-data; '
                f'name="{k}"; filename="{fname}"\r\n'
                f'Content-Type: {ctype}\r\n\r\n').encode()
        buf += data + b"\r\n"
    buf += f"--{boundary}--\r\n".encode()
    return bytes(buf), f"multipart/form-data; boundary={boundary}"


def kaggle_dataset_upload_new(owner, slug, title, zip_path,
                              license_name="CC0-1.0"):
    """Create a NEW Kaggle dataset from a local zip via the v1 REST API.

    Returns (ok, detail). Creates a public dataset under `owner`.
    """
    try:
        with open(zip_path, "rb") as f:
            zdata = f.read()
    except OSError as e:
        return False, f"cannot read zip: {e}"
    fields = {"title": title, "slug": slug, "licenseName": license_name,
              "isPrivate": "false"}
    body, ctype = multipart_encode(
        fields,
        {"file": (os.path.basename(zip_path), zdata, "application/zip")})
    return kaggle_request("POST", "/datasets/create/new", data=body,
                          headers={"Content-Type": ctype}, timeout=3600)


# ============================================================ v2: store dispatch
def verify_entry(entry, local_path):
    """Dispatch verification to the entry's store backend. v1 github path kept."""
    store = entry.get("store", "github")
    if store == "github":
        return verify_github_asset(
            entry.get("repo", ""), entry.get("release", ""),
            entry.get("asset", ""), entry.get("size", 0),
            entry.get("sha256", ""))
    if store == "terabox":
        return verify_terabox_entry(entry, local_path)
    if store == "kaggle":
        return verify_kaggle_entry(entry, local_path)
    return False, f"unknown store {store}"


def upload_to_store(store, local_path, dest):
    """Upload one local file to a store. Returns (ok, detail).

    dest for github:  {"repo": "o/r", "release": "tag"}
    dest for terabox: {"remote_dir": "/path", "remote_name": optional}
    dest for kaggle:  {"owner": ..., "slug": ..., "title": ..., "zip": bool}
      (kaggle uploads a zip of the file; caller prepares the zip)
    """
    if store == "github":
        repo = dest.get("repo", "")
        release = dest.get("release", "")
        if not repo or not release:
            return False, "github dest needs repo + release"
        rc, out, err = run(
            ["gh", "release", "upload", release, local_path,
             "--repo", repo, "--clobber"], timeout=3600)
        if rc != 0:
            tail = (err.strip().splitlines() or [f"exit {rc}"])[-1]
            return False, f"gh release upload failed: {tail[:200]}"
        return True, "gh release upload ok (verify step follows)"
    if store == "terabox":
        return terabox_upload(local_path, dest.get("remote_dir", "/"),
                              dest.get("remote_name"))
    if store == "kaggle":
        return kaggle_dataset_upload_new(
            dest.get("owner", ""), dest.get("slug", ""),
            dest.get("title", dest.get("slug", "")),
            local_path, dest.get("license", "CC0-1.0"))
    return False, f"unknown store {store}"


# ============================================================ v2: torbox helpers
def _torbox_surrogate():
    """Fetch the TorBox API surrogate via the authd exchange.

    The raw key never touches a file, argument, or log. Returns
    (ok, surrogate|detail). Lazy import so cloud_dog stays importable
    without the connector present.
    """
    try:
        sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
        from dynamic_credentials import dynamic_credential_entry
        entry = dynamic_credential_entry("custom.torbox")
        tok = str(entry.get("surrogate", "")).strip()
        if not tok:
            return False, "torbox surrogate empty"
        return True, tok
    except Exception as e:
        return False, f"torbox credential unavailable: {e}"


def torbox_api(path, params=None, timeout=60):
    """GET a TorBox v1 API path. Returns (ok, data|detail)."""
    import urllib.request
    import urllib.error
    import urllib.parse
    ok, tok = _torbox_surrogate()
    if not ok:
        return False, tok
    q = dict(params or {})
    q["token"] = tok  # requestdl-style endpoints need query placement
    url = TORBOX_API + "/" + path.lstrip("/") + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(
        url, headers={"User-Agent": UA_BROWSER, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"torbox HTTP {e.code}"
    except Exception as e:
        return False, f"torbox error: {e}"
    if not payload.get("success", False):
        return False, f"torbox: {payload.get('detail', 'unknown error')}"
    return True, payload.get("data")


def torbox_add_magnet(magnet):
    """Submit a magnet to TorBox cloud. Returns (ok, {torrent_id,hash,name}|detail)."""
    import urllib.request
    import urllib.parse
    ok, tok = _torbox_surrogate()
    if not ok:
        return False, tok
    form = urllib.parse.urlencode({"magnet": magnet, "token": tok}).encode()
    req = urllib.request.Request(
        TORBOX_API + "/torrents/createtorrent", data=form,
        headers={"User-Agent": UA_BROWSER, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return False, f"torbox add failed: {e}"
    if not payload.get("success"):
        return False, f"torbox: {payload.get('detail', 'unknown error')}"
    d = payload.get("data") or {}
    return True, {"torrent_id": d.get("torrent_id"),
                  "hash": d.get("hash"), "name": d.get("name")}


def torbox_wait_complete(torrent_id, poll_secs=60, timeout_secs=12 * 3600):
    """Poll until the TorBox torrent is fully downloaded. Returns (ok, info|detail)."""
    import time as _t
    deadline = _t.time() + timeout_secs
    while _t.time() < deadline:
        ok, data = torbox_api("torrents/mylist",
                              {"bypass_cache": "true", "id": torrent_id})
        if not ok:
            return False, data
        d = data if isinstance(data, dict) else {}
        state = str(d.get("download_state", ""))
        try:
            prog = float(d.get("progress") or 0)
        except (TypeError, ValueError):
            prog = 0
        if state.lower() == "completed" or prog >= 1.0:
            return True, d
        if state.lower() in ("failed", "error"):
            return False, f"torrent failed: {d.get('detail', '')}"
        _t.sleep(poll_secs)
    return False, "timeout waiting for torbox download"


def torbox_requestdl(torrent_id, file_id=""):
    """Get a CDN download link for a torrent (optionally one file).

    Returns (ok, link|detail)."""
    params = {"torrent_id": torrent_id}
    if file_id:
        params["file_id"] = file_id
    ok, data = torbox_api("torrents/requestdl", params)
    if not ok:
        return False, data
    if isinstance(data, dict):
        link = data.get("link") or data.get("url") or ""
        if link:
            return True, link
    return False, f"no download link in torbox response: {str(data)[:200]}"


# ============================================================ v2: streaming parts
def iter_file_parts(fobj, part_bytes, chunk=1 << 20):
    """Yield (index, bytes) parts from a binary file object.

    Pure function (no network) so the part-splitting logic is unit-testable.
    """
    idx = 0
    buf = bytearray()
    while True:
        data = fobj.read(chunk)
        if not data:
            break
        buf += data
        while len(buf) >= part_bytes:
            yield idx, bytes(buf[:part_bytes])
            del buf[:part_bytes]
            idx += 1
    if buf:
        yield idx, bytes(buf)


def stream_url_to_parts(url, staging_dir, part_bytes, chunk=8 << 20,
                        progress_cb=None):
    """Stream a URL into rolling part files on disk.

    Yields (index, part_path, size, sha256) as each part fills. Disk never
    holds more than part_bytes + chunk at a time; the caller is expected to
    upload+verify+delete each part before pulling the next (delete-as-you-go).
    Supports HTTP Range resume if the server honors it.
    """
    import urllib.request
    import urllib.error
    os.makedirs(staging_dir, exist_ok=True)
    offset = 0
    idx = 0
    buf = bytearray()
    hasher = None
    part_path = None
    part_f = None

    def _open_part(i):
        p = os.path.join(staging_dir, f"mirror-part-{i:04d}.bin")
        return p, open(p, "wb"), hashlib.sha256()

    exhausted = False
    while not exhausted:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA_BROWSER,
                          "Range": f"bytes={offset}-"})
        try:
            resp = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 416:  # past EOF
                break
            raise
        with resp:
            while True:
                data = resp.read(chunk)
                if not data:
                    exhausted = True
                    break
                offset += len(data)
                if part_f is None:
                    part_path, part_f, hasher = _open_part(idx)
                part_f.write(data)
                hasher.update(data)
                buf_len = part_f.tell()
                if buf_len >= part_bytes:
                    part_f.close()
                    digest = hasher.hexdigest()
                    yield idx, part_path, buf_len, digest
                    if progress_cb:
                        progress_cb(idx, buf_len)
                    idx += 1
                    part_f = None
    if part_f is not None:
        part_f.close()
        size = os.path.getsize(part_path)
        if size:
            yield idx, part_path, size, hasher.hexdigest()
            if progress_cb:
                progress_cb(idx, size)
        else:
            os.unlink(part_path)


# ============================================================ v2: mirror command
def cmd_mirror(args):
    """Torrent -> cloud store with bounded local footprint.

    Pipeline: TorBox cloud torrent -> CDN link -> Range-stream into rolling
    part files -> upload each part -> verify -> delete local part immediately
    -> manifest + registry entry. Disk never holds more than one part.
    """
    part_bytes = max(1, args.part_mb) * 1024 * 1024
    store = args.store
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    staging = os.path.join(MIRROR_STAGING, run_id)
    os.makedirs(staging, exist_ok=True)
    manifest = {"run_id": run_id, "source": {}, "store": store,
                "part_bytes": part_bytes, "files": [],
                "started_at": datetime.now(timezone.utc).isoformat()}

    def fail(msg):
        print(f"MIRROR FAIL: {msg}", file=sys.stderr)
        audit_log("mirror_fail", run_id=run_id, detail=msg[:300])
        return 1

    # 1. torrent source
    if args.magnet:
        ok, res = torbox_add_magnet(args.magnet)
        if not ok:
            return fail(res)
        torrent_id = res["torrent_id"]
        manifest["source"] = {"kind": "magnet", "torrent_id": torrent_id,
                              "name": res.get("name", "")}
        print(f"torbox: added '{res.get('name', '')}' id={torrent_id}")
    elif args.torrent_id:
        torrent_id = args.torrent_id
        manifest["source"] = {"kind": "torrent_id", "torrent_id": torrent_id}
    else:
        return fail("need --magnet or --torrent-id")

    # 2. wait for cloud download
    print("torbox: waiting for cloud download to complete...")
    ok, info = torbox_wait_complete(torrent_id, poll_secs=args.poll_secs)
    if not ok:
        return fail(info)
    files = info.get("files") or []
    if args.file:
        files = [f for f in files
                 if args.file.lower() in str(f.get("name", "")).lower()]
        if not files:
            return fail(f"no torrent file matches --file={args.file!r}")
    print(f"torbox: {len(files)} file(s) ready")

    # 3. store destination config
    if store == "github":
        if not args.repo or not args.release:
            return fail("github mirror needs --repo and --release")
        dest = {"repo": args.repo, "release": args.release}
    elif store == "terabox":
        dest = {"remote_dir": args.remote_dir or "/cloud-dog-mirror"}
    elif store == "kaggle":
        if not args.dataset:
            return fail("kaggle mirror needs --dataset owner/slug")
        owner = args.dataset.split("/")[0]
        dest = {"owner": owner, "slug": args.dataset.split("/")[-1],
                "title": args.title or args.dataset}
    else:
        return fail(f"unknown store {store}")

    # 4. stream each file -> parts -> upload/verify/delete
    total_up = 0
    reg = load_registry()
    try:
        for fi, f in enumerate(files):
            fname = str(f.get("name", f"file-{fi}"))
            fid = str(f.get("id", ""))
            fsize = int(f.get("size") or 0)
            ok, link = torbox_requestdl(torrent_id, fid)
            if not ok:
                print(f"  SKIP {fname}: {link}")
                audit_log("mirror_file_skip", run_id=run_id, file=fname,
                          detail=str(link)[:200])
                continue
            print(f"[{fi + 1}/{len(files)}] {fname} ({human(fsize)})")
            fentry = {"name": fname, "size": fsize, "parts": []}
            base = os.path.splitext(os.path.basename(fname))[0][:60] or "mirror"
            for pidx, ppath, psize, phash in stream_url_to_parts(
                    link, staging, part_bytes):
                asset = f"{base}.part-{pidx:04d}"
                if store == "github":
                    ok, detail = upload_to_store(
                        store, ppath,
                        {"repo": dest["repo"], "release": dest["release"]})
                    # rename asset to our part name via gh (uploaded under
                    # basename); verify by size+sha against the release
                    ok_v, detail_v = (False, "upload failed: " + detail)
                    if ok:
                        ok_v, detail_v = verify_github_asset(
                            dest["repo"], dest["release"],
                            os.path.basename(ppath), psize, phash)
                elif store == "terabox":
                    ok, detail = upload_to_store(
                        store, ppath,
                        {"remote_dir": dest["remote_dir"],
                         "remote_name": asset})
                    ok_v, detail_v = (ok, detail)
                    if ok:
                        ok_v, detail_v = verify_terabox_entry(
                            {"remote_dir": dest["remote_dir"],
                             "asset": asset, "size": psize}, ppath)
                else:  # kaggle: zip the part, one dataset per file
                    import zipfile
                    zpath = ppath + ".zip"
                    with zipfile.ZipFile(zpath, "w",
                                         zipfile.ZIP_DEFLATED) as z:
                        z.write(ppath, asset)
                    ok, detail = upload_to_store(
                        store, zpath,
                        {"owner": dest["owner"],
                         "slug": f"{dest['slug']}-{base}-{pidx:04d}"[:90],
                         "title": f"{dest['title']} {base} part {pidx:04d}"})
                    ok_v, detail_v = (ok, detail)
                    try:
                        os.unlink(zpath)
                    except OSError:
                        pass
                if not ok_v:
                    print(f"  part {pidx}: VERIFY FAILED - {detail_v}; "
                          f"local kept at {ppath}")
                    audit_log("mirror_part_unverified", run_id=run_id,
                              file=fname, part=pidx, detail=str(detail_v)[:200])
                    fentry["parts"].append({"index": pidx, "status": "unverified",
                                            "local": ppath, "size": psize,
                                            "sha256": phash})
                    continue
                # verified -> delete local part immediately (delete-as-you-go)
                try:
                    os.unlink(ppath)
                except OSError:
                    pass
                total_up += psize
                part_rec = {"index": pidx, "status": "verified",
                            "size": psize, "sha256": phash,
                            "asset": asset if store != "github"
                            else os.path.basename(ppath)}
                fentry["parts"].append(part_rec)
                # registry entry so `reap` can clean stragglers later
                rpath = f"mirror:{run_id}:{fname}:part-{pidx:04d}"
                reg["entries"][rpath] = {
                    "local_path": rpath, "store": store,
                    "repo": dest.get("repo", ""),
                    "release": dest.get("release", ""),
                    "remote_dir": dest.get("remote_dir", ""),
                    "dataset": args.dataset if store == "kaggle" else "",
                    "asset": part_rec["asset"], "sha256": phash,
                    "size": psize, "note": f"mirror {run_id} {fname}",
                    "registered_at": now_stamp(), "status": "reaped",
                }
                audit_log("mirror_part", run_id=run_id, file=fname,
                          part=pidx, size=psize, sha256=phash, store=store,
                          asset=part_rec["asset"])
                print(f"  part {pidx}: {human(psize)} verified on {store}, "
                      f"local deleted")
            manifest["files"].append(fentry)
    finally:
        save_registry(reg)
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest["bytes_uploaded"] = total_up
        mpath = os.path.join(AGENT_OPS, f"mirror-manifest-{run_id}.json")
        try:
            with open(mpath, "w") as f:
                json.dump(manifest, f, indent=2)
        except OSError:
            mpath = "(manifest write failed)"
        try:
            os.rmdir(staging)  # only succeeds when empty
        except OSError:
            pass
    msg = (f"mirror complete: {len(manifest['files'])} file(s), "
           f"{human(total_up)} uploaded+verified, manifest {mpath}")
    print(msg)
    log_action(f"cloud_dog mirror {run_id}: {msg}")
    audit_log("mirror_complete", run_id=run_id,
              files=len(manifest["files"]), bytes_uploaded=total_up,
              manifest=mpath)
    return 0


# ============================================================ v2: daemon command
def cmd_daemon(args):
    """Run the enforce loop continuously (guardian cadence without cron).

    Writes a pidfile, refuses a second instance, handles SIGTERM/SIGINT,
    and journals every cycle. Ctrl-C or `kill` stops it cleanly.
    """
    import signal
    import time as _t
    pidfile = os.path.abspath(args.pidfile) if args.pidfile else DAEMON_PIDFILE
    interval = max(60, args.interval)

    if os.path.exists(pidfile):
        try:
            with open(pidfile) as f:
                old = int(f.read().strip())
            os.kill(old, 0)  # raises if not running
            print(f"daemon already running as pid {old} ({pidfile})",
                  file=sys.stderr)
            return 1
        except (ValueError, OSError):
            pass  # stale pidfile; take over

    stop = {"flag": False}

    def _handle(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    try:
        with open(pidfile, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        print(f"cannot write pidfile {pidfile}: {e}", file=sys.stderr)
        return 1

    class _A:  # namespace for cmd_enforce
        pass
    ea = _A()
    ea.apply = args.apply
    ea.path = ""

    print(f"cloud_dog daemon: pid {os.getpid()}, every {interval}s, "
          f"apply={args.apply} (pidfile {pidfile})")
    audit_log("daemon_start", pid=os.getpid(), interval_secs=interval,
              apply=args.apply)
    log_action(f"cloud_dog daemon started (pid {os.getpid()}, "
               f"interval {interval}s, apply={args.apply})")
    try:
        while not stop["flag"]:
            t0 = _t.time()
            try:
                pct, _, _ = disk_usage_percent()
                cmd_enforce(ea)
                audit_log("daemon_cycle", disk_pct=round(pct, 1),
                          apply=args.apply, cycle_secs=round(_t.time() - t0, 1))
            except Exception as e:  # never let one bad cycle kill the daemon
                audit_log("daemon_cycle_error", detail=str(e)[:200])
            waited = 0
            while waited < interval and not stop["flag"]:
                _t.sleep(1)
                waited += 1
    finally:
        try:
            os.unlink(pidfile)
        except OSError:
            pass
        audit_log("daemon_stop", pid=os.getpid())
        log_action("cloud_dog daemon stopped")
        print("daemon stopped.")
    return 0


# ============================================================ v2: verify/reap dispatch
def cmd_verify_v2(args):
    """v2 verify: dispatch on the entry's store backend."""
    path = os.path.abspath(args.file)
    reg = load_registry()
    entry = reg["entries"].get(path)
    if not entry:
        print(f"No registry entry for {path}", file=sys.stderr)
        return 1
    if not os.path.exists(path):
        print(f"Local file missing: {path} (already cleared?)")
        return 2
    ok, detail = verify_entry(entry, path)
    print(f"{'OK' if ok else 'FAIL'}: {path} - {detail}")
    audit_log("verify", path=path, store=entry.get("store"),
              ok=ok, detail=detail[:200])
    return 0 if ok else 1


def cmd_reap_v2(args):
    """v2 reap: verify-then-delete across all store backends."""
    apply = args.apply
    search_root = os.path.abspath(args.path) if args.path else None
    reaped, skipped, failed = [], [], []
    for path, entry in reap_candidates(search_root):
        ok, detail = verify_entry(entry, path)
        if not ok:
            failed.append((path, detail))
            audit_log("reap_keep", path=path, store=entry.get("store"),
                      detail=detail[:200])
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
                msg = (f"REAPED {path} ({human(size)}) - verified on "
                       f"{entry.get('store')}:{entry.get('repo') or entry.get('remote_dir') or entry.get('dataset')}")
                log_action(msg)
                audit_log("reap", path=path, store=entry.get("store"),
                          size=size, sha256=entry.get("sha256", "")[:16],
                          dest=entry.get("repo") or entry.get("remote_dir")
                          or entry.get("dataset"))
                reaped.append((path, size))
            except OSError as e:
                failed.append((path, str(e)))
        else:
            reaped.append((path, size))
            print(f"WOULD-REAP {path} ({human(size)}) - {detail}")
    total = sum(s for _, s in reaped)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] reaped={len(reaped)} ({human(total)}) "
          f"failed-verify={len(failed)}")
    for path, detail in failed:
        print(f"  KEEP (unverified): {path} - {detail}")
    if apply and reaped:
        log_action(f"cloud_dog reap: {len(reaped)} files, {human(total)} freed")
        audit_log("reap_summary", count=len(reaped), bytes_freed=total)
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
    p.add_argument("--store", required=True, choices=STORES,
                   help="github | terabox | kaggle")
    p.add_argument("--repo", default="",
                   help="github: OWNER/REPO")
    p.add_argument("--release", default="",
                   help="github: release tag holding the asset")
    p.add_argument("--asset", default="",
                   help="remote filename on the store")
    p.add_argument("--remote-dir", default="",
                   help="terabox: remote directory, e.g. /CN-Gigaleak/show")
    p.add_argument("--dataset", default="",
                   help="kaggle: dataset slug as owner/name")
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

    p = sub.add_parser("mirror",
                       help="torrent -> cloud store, bounded local footprint")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--magnet", default="",
                     help="magnet URI to fetch via TorBox cloud")
    src.add_argument("--torrent-id", default="",
                     help="existing TorBox torrent id")
    p.add_argument("--store", required=True, choices=STORES,
                   help="destination store")
    p.add_argument("--repo", default="", help="github: OWNER/REPO")
    p.add_argument("--release", default="", help="github: release tag")
    p.add_argument("--remote-dir", default="/cloud-dog-mirror",
                   help="terabox: remote directory")
    p.add_argument("--dataset", default="",
                   help="kaggle: owner/slug for the dataset")
    p.add_argument("--title", default="", help="kaggle: dataset title")
    p.add_argument("--file", default="",
                   help="only mirror torrent files whose name contains this")
    p.add_argument("--part-mb", type=int, default=100,
                   help="rolling part size in MB (disk never holds more)")
    p.add_argument("--poll-secs", type=int, default=60,
                   help="torbox completion poll interval")

    p = sub.add_parser("daemon",
                       help="run the enforce loop continuously")
    p.add_argument("--interval", type=int, default=600,
                   help="seconds between cycles (min 60)")
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--pidfile", default="",
                   help="pidfile path (default agent-ops/cloud-dog-daemon.pid)")

    args = ap.parse_args(argv)
    try:
        if args.cmd == "scan":
            cmd_scan(args.json)
        elif args.cmd == "register":
            return cmd_register(args)
        elif args.cmd == "verify":
            return cmd_verify_v2(args)
        elif args.cmd == "reap":
            return cmd_reap_v2(args)
        elif args.cmd == "mirror":
            return cmd_mirror(args)
        elif args.cmd == "daemon":
            return cmd_daemon(args)
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
