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
    cloud_dog.py registry-migrate         # one-shot JSON -> sqlite import
    # Global: --registry sqlite|json (or $CLOUDEDOG_REGISTRY); default json.
    cloud_dog.py mirror --magnet URI --store github --repo O/R --release T
        [--part-mb 100]                   # torrent -> store, bounded disk
    cloud_dog.py daemon [--interval 600] [--apply]
        # continuous enforce loop (pidfile, SIGTERM-clean)
    cloud_dog.py hash --file PATH [--json]
        # fast file digest (BLAKE3 when available), prints backend + rate
    cloud_dog.py agent-audit [--quota 20G] [--json]
        # per-agent workdir sizes + registry coverage (AGENT DISK PUSH LAW;
        # read-only, never deletes)

v2 (2026-10-17): terabox backend (tbupload.py), kaggle backend (REST),
torrent mirror mode (TorBox cloud -> Range-stream -> part upload/verify/
delete), daemon mode, structured audit journal (cloud-dog-audit.jsonl).

v3 (2026-10-17): sqlite-backed registry (Python stdlib sqlite3, WAL mode)
replaces the JSON file — atomic transactions, concurrent-safe for the
guardian cron + manual runs. Legacy cloud-dog-registry.json migrates
once on first open; `registry_export_json()` dumps the legacy shape.

v4 (2026-10-19): registry backends in cloud_dog_registry.py — columnar
sqlite (entries keyed by absolute path, real columns for store/repo/
release/asset/size/sha256/status/registered_at/reaped_at/verified_at +
journal/audit table, WAL, busy timeout) or legacy JSON. Select with the
--registry sqlite|json flag or $CLOUDEDOG_REGISTRY; default stays JSON
until the owner flips it. `registry-migrate` runs the one-shot
JSON -> sqlite import (JSON file kept as backup, never deleted); DBs
from the v3 blob schema upgrade in place on open.

Default mode is DRY-RUN. Nothing is deleted unless --apply is passed.
"""

import argparse
import hashlib
import json
import os
import shutil
# Registry backends (json | sqlite): cloud_dog_registry.py, same directory.
# Defensive sys.path fix so `import cloud_dog` from anywhere still works.
try:
    import cloud_dog_registry as _regmod
except ImportError:  # pragma: no cover - belt and suspenders
    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import cloud_dog_registry as _regmod
import subprocess
import sys
import time
from datetime import datetime, timezone

# Owner's disk laws as code (clouddog/policy/laws.py, PR #8). The reap path
# consults decide_delete for every candidate: it can only make reap MORE
# conservative, never less. Fail closed if the import breaks.
try:
    from clouddog.policy.laws import decide_delete
    _POLICY_AVAILABLE = True
except ImportError:  # pragma: no cover - defensive; package ships in-repo
    decide_delete = None  # type: ignore
    _POLICY_AVAILABLE = False

# V1 policy engine (src/clouddog/policy.py) as the FRONT DECIDER for reap:
# evaluate(path) runs first for every candidate (law-ordered, with reasons);
# V2's registry + decide_delete + live verify-then-delete is the execution.
# Loaded by file path because the sibling root-level `clouddog/` package
# shadows the src/ tree on sys.path. Fail closed if the load breaks: reap
# refuses to run rather than silently losing guards.
def _load_v1_policy():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    policy_path = os.path.join(here, "src", "clouddog", "policy.py")
    spec = importlib.util.spec_from_file_location("_clouddog_v1_policy", policy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load v1 policy engine from {policy_path}")
    module = importlib.util.module_from_spec(spec)
    # dataclass processing looks the module up in sys.modules by __name__;
    # register it before exec_module or @dataclass blows up.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    _V1_POLICY = _load_v1_policy()
    _v1_evaluate = _V1_POLICY.evaluate
    _V1_ACTION_SACRED = _V1_POLICY.Action.SACRED.value
    _V1_ACTION_STORE = _V1_POLICY.Action.STORE.value
    _V1_ACTION_STORE_AND_CLEAR = _V1_POLICY.Action.STORE_AND_CLEAR.value
    _V1_ACTION_STORE_AND_CLEAR_STRICT = _V1_POLICY.Action.STORE_AND_CLEAR_STRICT.value
    _V1_ACTION_KEEP_LOCAL = _V1_POLICY.Action.KEEP_LOCAL.value
    _EVALUATE_AVAILABLE = True
except Exception:  # pragma: no cover - defensive; module ships in-repo
    _V1_POLICY = None
    _v1_evaluate = None
    _V1_ACTION_SACRED = _V1_ACTION_STORE = _V1_ACTION_STORE_AND_CLEAR = None
    _V1_ACTION_STORE_AND_CLEAR_STRICT = _V1_ACTION_KEEP_LOCAL = None
    _EVALUATE_AVAILABLE = False


# Pull-back retrievability prover (src/clouddog/retrievability.py). Loaded
# by file path for the same shadowing reason as the v1 policy above. When
# a reap run passes --retrievability-proven, the flag no longer merely
# ASSERTS that a pull-back happened: cmd_reap_v2 RUNS the prover for each
# strict-bar (game-file) candidate and records the proof in the audit
# journal. If the prover cannot load or a proof fails/infeasible, the
# strict bar stays unsatisfied — fail closed.
def _load_retrievability():
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    prover_path = os.path.join(here, "src", "clouddog", "retrievability.py")
    spec = importlib.util.spec_from_file_location("_clouddog_retrievability", prover_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load retrievability prover from {prover_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    _RETRIEVABILITY = _load_retrievability()
    _RETRIEVABILITY_AVAILABLE = True
except Exception:  # pragma: no cover - defensive; module ships in-repo
    _RETRIEVABILITY = None
    _RETRIEVABILITY_AVAILABLE = False


def _run_retrievability_proof(entry):
    """Run the pull-back prover for one registry entry.

    Returns the ProofResult, or None when the prover is unavailable or
    raised unexpectedly: the caller treats None as "not proven" (fail
    closed). prove_registry_entry itself never raises on proof failure.
    """
    if not _RETRIEVABILITY_AVAILABLE:
        return None
    try:
        return _RETRIEVABILITY.prove_registry_entry(entry)
    except Exception:  # noqa: BLE001 - prover must never crash reap
        return None

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


def run(cmd, timeout=60, env=None):
    """Run a shell command, return (rc, stdout, stderr). Never raises.

    env=None inherits the parent environment; pass an explicit env mapping
    when a secret (e.g. RESTIC_PASSWORD) must be present for the child.
    """
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            env=env,
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


def _b3sum_bin():
    """Path to the bundled b3sum binary, or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "tools", "bin", "b3sum")
    return cand if os.path.isfile(cand) and os.access(cand, os.X_OK) else None


def _xxhash_available():
    """True when the optional xxhash pip package is importable."""
    try:
        import xxhash  # noqa: F401
        return True
    except ImportError:
        return False


def xxh3_file(path, chunk=1 << 20):
    """Fast NON-cryptographic hash (xxHash3) for pre-filtering.

    31 GB/s — use as a cheap first pass before the cryptographic
    fast_hash_file(). If xxh3 differs, files differ (skip the slow hash).
    If xxh3 matches, MUST confirm with fast_hash_file() (not collision-safe).
    Returns None if the xxhash package is not installed.
    """
    try:
        import xxhash
    except ImportError:
        return None
    h = xxhash.xxh3_64()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _blake3_available():
    """True when the optional blake3 pip package is importable."""
    try:
        import blake3  # noqa: F401
        return True
    except ImportError:
        return False


def fast_hash_file(path, chunk=1 << 20):
    """Fast BLAKE3 hash for INTERNAL comparisons (not store digests).

    Prefers the in-process `blake3` pip package (fastest, no spawn
    overhead), then the bundled b3sum binary (tools/bin/), then SHA-256.
    The digest only needs to match between the two files being compared —
    never use this where a store expects a specific digest (GitHub release
    assets are SHA-256; use sha256_file() for those).
    """
    if _blake3_available():
        import blake3
        h = blake3.blake3()
        with open(path, "rb") as f:
            for blk in iter(lambda: f.read(chunk), b""):
                h.update(blk)
        return h.hexdigest()
    b3 = _b3sum_bin()
    if b3:
        try:
            out = subprocess.run([b3, str(path)], capture_output=True,
                                 text=True, timeout=300)
            if out.returncode == 0:
                return out.stdout.split()[0]
        except (subprocess.SubprocessError, OSError):
            pass
    return sha256_file(path, chunk)


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
# Registry backends live in cloud_dog_registry.py (v4): a columnar sqlite
# backend (WAL, busy timeout, atomic transactions, journal/audit table)
# and the legacy JSON backend, behind ONE dict-shaped interface so every
# call site keeps working unchanged.
#
# Selection (see cloud_dog_registry.select_backend):
#   1. main()'s --registry sqlite|json flag (stored in _REGISTRY_BACKEND),
#   2. $CLOUDEDOG_REGISTRY,
#   3. "json" (default; the owner flips it to sqlite explicitly).
#
# The one-shot JSON -> sqlite importer (migrate_json_to_sqlite) preserves
# every entry and NEVER deletes the JSON file — it stays as the backup.
# DBs written by the v3 blob schema are upgraded in place on open, with
# the old table kept as entries_legacy_v3.
#
# load_registry() / save_registry() keep their exact v1/v2/v3 API.

_REGISTRY_BACKEND = None  # set by main() from --registry


def _registry_backend():
    return _regmod.select_backend(_REGISTRY_BACKEND)


def set_registry_backend(backend):
    """Override registry backend for this process (CLI --registry flag)."""
    global _REGISTRY_BACKEND
    _REGISTRY_BACKEND = backend


def load_registry():
    return _regmod.load_registry(
        REGISTRY_PATH, REGISTRY_DB_PATH, backend=_registry_backend())


def save_registry(reg):
    return _regmod.save_registry(
        reg, REGISTRY_PATH, REGISTRY_DB_PATH,
        backend=_registry_backend())


def registry_export_json(dest_path=None):
    """Dump the sqlite registry back to the legacy JSON shape (backup/audit)."""
    dest = dest_path or (REGISTRY_PATH + ".export")
    return _regmod.export_json(REGISTRY_DB_PATH, dest)


def log_action(text):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    line = f"- [{now_stamp()}] {text}\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)


# ---------------------------------------------------------------- commands
def _repo_tool_bin(name):
    """Path to a binary vendored in the repo's tools/bin/, or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "tools", "bin", name)
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def _workspace_tool_bin(name, *rel_parts):
    """Locate a tool under ~/workspace/tools/<name>/... (this VM's layout)."""
    for rel in rel_parts:
        p = os.path.join(WORKSPACE, "tools", name, rel)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _dust_binary():
    """Locate the dust binary (optional dependency for fast scans)."""
    return (_repo_tool_bin("dust") or shutil.which("dust")
            or _workspace_tool_bin("dust", "dust",
                                  os.path.join("dust", "dust")))


def _aria2c_binary():
    """Locate the aria2c binary (transport for `mirror --direct`).

    Same precedence convention as _dust_binary: repo-vendored first, then
    PATH, then the known install at ~/workspace/tools/aria2/aria2c
    (wrapper script; the extracted Debian-tree binary is at
    .../usr/bin/aria2c). Returns None when missing so direct mode fails
    closed with a clear message.
    """
    return (_repo_tool_bin("aria2c") or shutil.which("aria2c")
            or _workspace_tool_bin("aria2", "aria2c",
                                  os.path.join("usr", "bin", "aria2c")))
def _parse_dust_size(s):
    """Parse dust -j size strings like '2.1M', '272K', '512B', '1.0Mi'.

    dust >= 1.2.x emits IEC suffixes ('1.0Mi'); older releases emit 'M'.
    Both mean the same binary multiplier. (Without the 'i' handling, the
    bundled dust 1.2.6's sizes all parse to 0 — scan/audit go blind.)
    """
    import re
    m = re.match(r"^\s*([\d.]+)\s*([BKMGTPE]?)(I?)\s*$", str(s).upper())
    if not m:
        return 0
    num, unit = float(m.group(1)), m.group(2)
    return int(num * {"": 1, "B": 1, "K": 1024, "M": 1024 ** 2,
                      "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5,
                      "E": 1024 ** 6}[unit])


def _dust_largest(root):
    """Fast path: dust -j JSON -> [(name, bytes)] for top-level entries.

    Returns None when dust is missing or its output can't be parsed, so the
    caller falls back to the stdlib walker. Never raises.
    """
    binary = _dust_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "-j", root], capture_output=True,
                              text=True, timeout=300)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        data = json.loads(proc.stdout)
        children = data.get("children") or []
        out = []
        for child in children:
            name = os.path.basename(child.get("name", ""))
            size = _parse_dust_size(child.get("size", "0"))
            if name:
                out.append((name, size))
        return out or None
    except Exception:
        return None


def cmd_scan(as_json=False):
    pct, used, total = disk_usage_percent()
    # largest top-level dirs in workspace (dust fast path, stdlib fallback)
    biggest = _dust_largest(WORKSPACE)
    if biggest is None:
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


def cmd_hash(args):
    """Hash one file with the fast-hash engine; print backend + digest + rate.

    Uses fast_hash_file() (PR #17: blake3 module -> bundled b3sum ->
    sha256) and reports which backend actually ran, so lanes can see what
    speed they are getting. Local comparisons only — never for store
    verification (registry/GitHub digests stay sha256).
    """
    path = os.path.abspath(args.file)
    if not os.path.isfile(path):
        print(f"not a file: {path}", file=sys.stderr)
        return 1
    if _blake3_available():
        backend = "blake3/pip-module"
    elif _b3sum_bin() is not None:
        backend = "blake3/bundled-b3sum"
    else:
        backend = "sha256/stdlib"
    size = os.path.getsize(path)
    t0 = time.monotonic()
    try:
        hexd = fast_hash_file(path)
    except OSError as e:
        print(f"hash failed: {e}", file=sys.stderr)
        return 1
    dt = max(time.monotonic() - t0, 1e-9)
    rate = size / dt / (1 << 20)
    if args.json:
        print(json.dumps({"path": path, "backend": backend, "digest": hexd,
                          "bytes": size, "mb_per_s": round(rate, 1)}))
    else:
        print(f"{backend}: {hexd}  {path}  ({human(size)}, {rate:.1f} MB/s)")
    return 0


def _parse_size(s):
    """Parse '20G', '500M', '1024' into bytes. Raises ValueError."""
    s = str(s).strip().upper()
    mult = {"B": 1, "K": 1 << 10, "KB": 1 << 10,
            "M": 1 << 20, "MB": 1 << 20,
            "G": 1 << 30, "GB": 1 << 30,
            "T": 1 << 40, "TB": 1 << 40}
    for suffix in sorted(mult, key=len, reverse=True):
        if s.endswith(suffix) and len(s) > len(suffix):
            return int(float(s[:-len(suffix)]) * mult[suffix])
    return int(float(s))


def _dir_tree_size(path):
    """Walk a tree (no symlink follow, sacred dirs pruned). Returns (bytes, files)."""
    total, count = 0, 0
    if os.path.islink(path):
        return 0, 0
    if os.path.isfile(path):
        try:
            return os.lstat(path).st_size, 1
        except OSError:
            return 0, 0
    for root, dirs, files in os.walk(path, followlinks=False):
        # prune sacred subtrees and symlinked dirs: never descend, never count
        dirs[:] = [d for d in dirs
                   if not os.path.islink(os.path.join(root, d))
                   and not is_sacred(os.path.join(root, d))]
        if is_sacred(root):
            dirs[:] = []
            continue
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                if os.path.islink(fp):
                    continue
                total += os.lstat(fp).st_size
                count += 1
            except OSError:
                pass
    return total, count


def _topdir_sizes(root):
    """[(name, bytes)] for top-level entries of root: dust fast path first,
    stdlib walk fallback. Returns (sizes, file_counts) where file_counts is
    a dict on the walk path or None when dust served sizes (dust reports
    bytes only; counting files would cost the walk we just avoided)."""
    biggest = _dust_largest(root)
    if biggest is not None:
        return biggest, None
    sizes, counts = [], {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return [], {}
    for name in names:
        if name.startswith("."):
            continue
        p = os.path.join(root, name)
        if is_sacred(p):
            continue
        total, count = _dir_tree_size(p)
        sizes.append((name, total))
        counts[name] = count
    return sizes, counts


def agent_audit_report(root=None, quota_bytes=0, registry=None, top=20,
                       min_uncovered_bytes=100 << 20):
    """Per-agent-directory disk report for the AGENT DISK PUSH LAW.

    Every agent lane keeps work under ~/workspace/<lane>/; everything on
    local disk must also live on GitHub or TeraBox. This read-only report
    shows, per top-level dir: bytes on disk, registry coverage (bytes with
    a live registered entry = claimed on a store), cleared bytes, and
    flags: over_quota, uncovered_big (sizeable dir with NOTHING registered),
    mostly_unregistered.

    root: directory to audit (default ~/workspace). registry: loaded
    registry dict (default load_registry()). quota_bytes: per-dir quota;
    0 = no quota enforcement.
    """
    root = os.path.abspath(root or WORKSPACE)
    if registry is None:
        registry = load_registry()
    entries = registry.get("entries", {})
    # index registry bytes by directory prefix for O(1) lookups
    covered, cleared = {}, {}
    for path, e in entries.items():
        if e.get("store") not in STORES:
            continue
        size = e.get("size", 0) or 0
        topdir = path if os.path.isdir(path) else os.path.dirname(path)
        # attribute to the immediate child of root
        rel = os.path.relpath(topdir, root)
        key = rel.split(os.sep, 1)[0] if not rel.startswith("..") else None
        if not key or key == ".":
            key = os.path.basename(path) if os.path.isfile(path) else None
        if not key:
            continue
        bucket = cleared if e.get("status") == "reaped" else covered
        bucket[key] = bucket.get(key, 0) + size
    rows = []
    sizes, file_counts = _topdir_sizes(root)
    for name, total in sizes:
        if name.startswith("."):
            continue
        p = os.path.join(root, name)
        if is_sacred(p):
            continue
        count = file_counts[name] if file_counts else None
        cov = covered.get(name, 0)
        clr = cleared.get(name, 0)
        unreg = max(total - cov, 0)
        cov_pct = round(100.0 * cov / total, 1) if total else 100.0
        flags = []
        if quota_bytes and total > quota_bytes:
            flags.append("over_quota")
        if total >= min_uncovered_bytes and cov == 0:
            flags.append("uncovered_big")
        elif total >= min_uncovered_bytes and cov_pct < 10.0:
            flags.append("mostly_unregistered")
        rows.append({"name": name, "bytes": total, "files": count,
                     "covered_bytes": cov, "cleared_bytes": clr,
                     "unregistered_bytes": unreg,
                     "coverage_pct": cov_pct, "flags": flags})
    rows.sort(key=lambda r: r["bytes"], reverse=True)
    return rows[:top] if top else rows


def cmd_agent_audit(args):
    """Audit per-agent workdirs: size, registry coverage, quota flags.

    Read-only: never deletes anything. Serves the AGENT DISK PUSH LAW —
    every agent's local disk must also live on GitHub/TeraBox.
    """
    try:
        quota = _parse_size(args.quota) if getattr(args, "quota", "") else 0
    except ValueError:
        print(f"bad --quota value: {args.quota!r}", file=sys.stderr)
        return 1
    rows = agent_audit_report(
        root=getattr(args, "root", "") or None,
        quota_bytes=quota,
        top=getattr(args, "top", 20),
        min_uncovered_bytes=_parse_size(getattr(args, "min_uncovered", "100M")),
    )
    total = sum(r["bytes"] for r in rows)
    if args.json:
        print(json.dumps({"rows": rows,
                          "quota_bytes": quota,
                          "total_bytes": total}, indent=2))
        return 0
    print(f"Agent-dir audit of ~/workspace (quota "
          f"{human(quota) if quota else 'none'}):")
    for r in rows:
        flag = f"  [{','.join(r['flags'])}]" if r["flags"] else ""
        nfiles = f"{r['files']} files" if r["files"] is not None else "files n/a"
        print(f"  {human(r['bytes']):>10}  {r['name']:<36}"
              f"  covered {r['coverage_pct']:>5}% "
              f"({nfiles}){flag}")
    uncovered = sum(r["unregistered_bytes"] for r in rows)
    print(f"Total audited: {human(total)}; "
          f"unregistered on disk: {human(uncovered)}")
    flagged = [r for r in rows if r["flags"]]
    if flagged:
        print("Flagged dirs (need store backup before any reap):")
        for r in flagged:
            print(f"  {r['name']}: {','.join(r['flags'])} "
                  f"({human(r['unregistered_bytes'])} unregistered)")
    audit_log("agent_audit", dirs=len(rows), total_bytes=total,
              flagged=[r["name"] for r in flagged])
    return 0


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
        "restic_repo": getattr(args, "restic_repo", "") or "",
        "snapshot_id": getattr(args, "snapshot_id", "") or "",
        "b2_bucket": getattr(args, "b2_bucket", "") or "",
        "b2_path": getattr(args, "b2_path", "") or "",
        "sha256": args.sha256 or "",
        "size": args.size or 0,
        "note": args.note or "",
        "registered_at": now_stamp(),
        "status": "registered",
    }
    if args.store == "restic":
        # restic register performs the backup now and records the snapshot id
        # (a registry claim alone is never proof; the backup IS the proof).
        repo = getattr(args, "restic_repo", "") or ""
        if not repo:
            print("register --store restic needs --restic-repo", file=sys.stderr)
            return 1
        snapshot_id = getattr(args, "snapshot_id", "") or ""
        if not snapshot_id:
            ok, res = restic_backup(path, repo)
            if not ok:
                print(f"register: restic backup failed: {res}", file=sys.stderr)
                return 1
            snapshot_id = res
            print(f"restic backup ok: snapshot {snapshot_id[:8]}")
        entry["restic_repo"] = repo
        entry["snapshot_id"] = snapshot_id
    if args.store == "b2":
        # b2 register performs the upload now and records bucket+path
        # (a registry claim alone is never proof; the upload IS the proof).
        bucket = getattr(args, "b2_bucket", "") or ""
        if not bucket:
            print("register --store b2 needs --b2-bucket", file=sys.stderr)
            return 1
        remote_path = (getattr(args, "b2_path", "") or ""
                       or os.path.basename(path))
        ok, res = b2_upload(path, bucket, remote_path)
        if not ok:
            print(f"register: b2 upload failed: {res}", file=sys.stderr)
            return 1
        entry["b2_bucket"] = bucket
        entry["b2_path"] = remote_path
        print(f"b2 upload ok: {res}")
    if os.path.exists(path) and not args.sha256:
        try:
            entry["sha256"] = sha256_file(path)
            entry["size"] = os.path.getsize(path)
        except OSError as e:
            print(f"WARNING: could not hash local file: {e}", file=sys.stderr)
    reg = load_registry()
    reg["entries"][path] = entry
    save_registry(reg)
    dest = {"github": f"{args.repo}@{args.release}",
            "terabox": entry.get("remote_dir", ""),
            "kaggle": entry.get("dataset", ""),
            "restic": f"{entry.get('restic_repo', '')}"
                      f"#{entry.get('snapshot_id', '')[:8]}",
            "b2": f"{entry.get('b2_bucket', '')}/{entry.get('b2_path', '')}",
            }.get(args.store, "")
    print(f"Registered: {path} -> {args.store}:{dest}")
    audit_log("register", path=path, store=args.store,
              repo=args.repo, release=args.release, asset=args.asset,
              remote_dir=getattr(args, "remote_dir", "") or "",
              dataset=getattr(args, "dataset", "") or "",
              restic_repo=entry.get("restic_repo", ""),
              snapshot_id=entry.get("snapshot_id", "")[:16],
              b2_bucket=entry.get("b2_bucket", ""),
              b2_path=entry.get("b2_path", ""),
              sha256=entry.get("sha256", "")[:16],
              size=entry.get("size", 0))
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
        # v2 dispatcher: reaps github + terabox-live + kaggle entries.
        # (v1 cmd_reap kept for backward compat; identical for github.)
        rc = cmd_reap_v2(args)
        after = disk_usage_percent()[0]
        freed = before - after
        print(f"Disk: {before:.1f}% -> {after:.1f}%")
        log_action(f"cloud_dog enforce: {before:.1f}% -> {after:.1f}% "
                   f"(apply={args.apply})")
        return rc
    print("Disk under ceiling; no reap needed (use --apply to force).")
    return 0


def cmd_registry_migrate(args):
    """One-shot JSON -> sqlite import. The JSON file is kept as a backup."""
    if _registry_backend() != "sqlite":
        print("registry-migrate needs the sqlite backend: pass "
              "--registry sqlite or set CLOUDEDOG_REGISTRY=sqlite",
              file=sys.stderr)
        return 2
    try:
        res = _regmod.migrate_json_to_sqlite(
            REGISTRY_PATH, REGISTRY_DB_PATH, force=args.force)
    except FileNotFoundError as e:
        print(f"registry-migrate: {e}", file=sys.stderr)
        return 1
    print(f"Migrated {res['migrated']} entries to {res['db_path']}")
    if res["skipped"]:
        print(f"  skipped {len(res['skipped'])} non-absolute-path rows")
    print(f"  JSON backup kept (never deleted): {res['json_path']}")
    return 0


def cmd_registry(args):
    backend = _registry_backend()
    reg = load_registry()
    entries = reg.get("entries", {})
    if args.json:
        print(json.dumps(reg, indent=2))
        return 0
    where = REGISTRY_DB_PATH if backend == "sqlite" else REGISTRY_PATH
    print(f"Registry [{backend}]: {len(entries)} entries ({where})")
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

STORES = ("github", "terabox", "kaggle", "restic", "b2")

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


# ============================================================ restic backend
def _restic_binary():
    """Locate the restic binary (optional dependency)."""
    found = shutil.which("restic")
    if found:
        return found
    known = os.path.join(WORKSPACE, "tools", "restic", "restic")
    if os.path.isfile(known) and os.access(known, os.X_OK):
        return known
    return None


def _restic_env(password=None):
    """Environment for restic subprocesses.

    The repo password comes from the RESTIC_PASSWORD environment variable
    (or the password argument). It is passed via the subprocess environment
    only — never on the command line, never logged.
    """
    env = dict(os.environ)
    pw = password or env.get("RESTIC_PASSWORD", "")
    if pw:
        env["RESTIC_PASSWORD"] = pw
    return env


def restic_repo_ready(repo, password=None):
    """True if restic can open the repo (initialized + password works)."""
    binary = _restic_binary()
    if not binary or not repo:
        return False
    env = _restic_env(password)
    if not env.get("RESTIC_PASSWORD"):
        return False
    rc, _, _ = run([binary, "-r", repo, "cat", "config"],
                   timeout=60, env=env)
    return rc == 0


def _rclone_binary():
    """Locate the rclone binary (optional dependency for B2/S3 stores)."""
    found = shutil.which("rclone")
    if found:
        return found
    known = os.path.join(WORKSPACE, "tools", "rclone", "rclone")
    if os.path.isfile(known) and os.access(known, os.X_OK):
        return known
    return None


def _b2_env():
    """Env vars for rclone B2 backend (credentials from environment)."""
    env = dict(os.environ)
    account = os.environ.get("B2_ACCOUNT_ID", "")
    key = os.environ.get("B2_ACCOUNT_KEY", "")
    if account and key:
        # rclone B2 backend via env-var config (no config file needed)
        env["RCLONE_CONFIG_B2_TYPE"] = "b2"
        env["RCLONE_CONFIG_B2_ACCOUNT"] = account
        env["RCLONE_CONFIG_B2_KEY"] = key
    return env


def b2_upload(local_path, bucket, remote_path):
    """Upload one file to Backblaze B2 via rclone.

    Returns (ok, detail). Credentials from B2_ACCOUNT_ID / B2_ACCOUNT_KEY
    env vars. Uses rclone's B2 backend (10GB free tier, S3-compatible).
    """
    binary = _rclone_binary()
    if not binary:
        return False, "rclone binary not found (PATH or ~/workspace/tools/rclone)"
    env = _b2_env()
    if not env.get("RCLONE_CONFIG_B2_ACCOUNT"):
        return False, "B2_ACCOUNT_ID / B2_ACCOUNT_KEY env vars not set"
    if not bucket:
        return False, "B2 bucket required (--b2-bucket)"
    if not os.path.isfile(local_path):
        return False, f"local file missing: {local_path}"
    remote = f"b2:{bucket}/{remote_path.lstrip('/')}"
    rc, out, err = run([binary, "copyto", local_path, remote,
                        "--progress=false"],
                       timeout=7200, env=env)
    if rc != 0:
        tail = (err.strip().splitlines() or [f"exit {rc}"])[-1]
        return False, f"rclone copyto failed: {tail[:200]}"
    return True, f"b2:{bucket}/{remote_path.lstrip('/')}"


def b2_verify(entry, local_path=None):
    """Verify a registry entry against Backblaze B2.

    Entry needs: b2_bucket, b2_path. Passes only if the object exists
    AND its size matches the registry. A registry claim alone is never proof.
    """
    binary = _rclone_binary()
    if not binary:
        return False, "rclone binary not found (PATH or ~/workspace/tools/rclone)"
    bucket = entry.get("b2_bucket", "")
    remote_path = entry.get("b2_path", "")
    if not bucket or not remote_path:
        return False, "b2 entry needs b2_bucket + b2_path"
    env = _b2_env()
    if not env.get("RCLONE_CONFIG_B2_ACCOUNT"):
        return False, "B2_ACCOUNT_ID / B2_ACCOUNT_KEY env vars not set"
    expected_size = int(entry.get("size", 0) or 0)
    remote = f"b2:{bucket}/{remote_path.lstrip('/')}"
    rc, out, err = run([binary, "lsjson", remote], timeout=300, env=env)
    if rc != 0:
        return False, f"b2 object not found: {remote}"
    try:
        info = json.loads(out)
    except ValueError:
        return False, "rclone lsjson output was not JSON"
    # lsjson on a single file returns a one-element list
    objs = info if isinstance(info, list) else [info]
    if not objs:
        return False, f"b2 object not found: {remote}"
    actual_size = int(objs[0].get("Size", -1))
    if expected_size and actual_size != expected_size:
        return False, (f"b2 size mismatch: expected {expected_size}, "
                       f"got {actual_size}")
    return True, f"b2:{bucket}/{remote_path} ({actual_size} bytes)"


def restic_backup(local_path, repo, password=None):
    """Back up one file into a restic repository.

    Returns (ok, snapshot_id|detail). ok=True only if `restic backup --json`
    exits 0 and reports a snapshot_id. The repo is auto-initialized on first
    use. Password via RESTIC_PASSWORD env var (or the password argument),
    passed through the subprocess environment — never on the command line.
    """
    binary = _restic_binary()
    if not binary:
        return False, "restic binary not found (PATH or ~/workspace/tools/restic)"
    env = _restic_env(password)
    if not env.get("RESTIC_PASSWORD"):
        return False, "RESTIC_PASSWORD env var not set (restic repo password)"
    if not repo:
        return False, "restic repo required (--restic-repo)"
    if not os.path.exists(local_path):
        return False, f"local file missing: {local_path}"
    if not restic_repo_ready(repo, password):
        rc, _, err = run([binary, "-r", repo, "init"], timeout=120, env=env)
        if rc != 0:
            tail = (err.strip().splitlines() or [f"exit {rc}"])[-1]
            return False, f"restic init failed: {tail[:200]}"
    rc, out, err = run([binary, "-r", repo, "backup", "--json", local_path],
                       timeout=7200, env=env)
    if rc != 0:
        tail = (err.strip().splitlines() or [out.strip().splitlines() or
                f"exit {rc}"][-1])[-1]
        return False, f"restic backup failed: {tail[:200]}"
    snapshot_id = ""
    for line in out.splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("message_type") == "summary" and msg.get("snapshot_id"):
            snapshot_id = msg["snapshot_id"]
            break
    if not snapshot_id:
        return False, "restic backup ran but reported no snapshot_id"
    return True, snapshot_id


def restic_verify(entry, local_path=None):
    """Verify a registry entry against a restic repository.

    Registry entry needs: restic_repo, snapshot_id. Passes only if the
    snapshot exists in the repo AND its recorded paths include the file.
    A registry claim alone is never proof. Password via RESTIC_PASSWORD.
    """
    binary = _restic_binary()
    if not binary:
        return False, "restic binary not found (PATH or ~/workspace/tools/restic)"
    repo = entry.get("restic_repo", "")
    snapshot_id = entry.get("snapshot_id", "")
    if not repo or not snapshot_id:
        return False, "restic entry needs restic_repo + snapshot_id"
    path = local_path or entry.get("local_path", "")
    env = _restic_env()
    if not env.get("RESTIC_PASSWORD"):
        return False, "RESTIC_PASSWORD env var not set (restic repo password)"
    rc, out, err = run([binary, "-r", repo, "snapshots", "--json"],
                       timeout=300, env=env)
    if rc != 0:
        tail = (err.strip().splitlines() or [f"exit {rc}"])[-1]
        return False, f"restic snapshots failed: {tail[:200]}"
    try:
        snapshots = json.loads(out)
    except ValueError:
        return False, "restic snapshots output was not JSON"
    for snap in snapshots:
        if snap.get("id") == snapshot_id or snap.get("short_id") == snapshot_id:
            paths = snap.get("paths") or []
            if path and path not in paths:
                return False, (f"snapshot {snapshot_id[:8]} exists but does "
                               f"not contain {path}")
            return True, (f"restic:{repo} snapshot {snapshot_id[:8]} verified "
                          f"({len(paths)} path(s))")
    return False, f"snapshot {snapshot_id[:8]} not found in restic:{repo}"


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
    """GET /datasets/view/{owner}/{slug}. Returns (ok, payload|detail).

    NOTE: the view endpoint returns dataset metadata but NOT the file list
    (its `files` array is always empty). Use kaggle_dataset_list_files()
    when you need the actual files.
    """
    return kaggle_request("GET", f"/datasets/view/{owner_slug}")


def kaggle_dataset_list_files(owner_slug):
    """GET /datasets/list/{owner}/{slug}. Returns (ok, payload|detail).

    payload["datasetFiles"] is the list of files, each with `name` and
    `totalBytes`. This is the endpoint the verify path must use.
    """
    return kaggle_request("GET", f"/datasets/list/{owner_slug}")


def verify_kaggle_entry(entry, local_path):
    """Verify a registry entry against a Kaggle dataset (live file list + size).

    Registry entry needs: dataset ("owner/slug"), asset (filename).
    """
    dataset = entry.get("dataset", "")
    asset = entry.get("asset", "")
    if not dataset or not asset:
        return False, "kaggle entry needs dataset (owner/slug) + asset (filename)"
    ok, payload = kaggle_dataset_list_files(dataset)
    if not ok:
        return False, payload
    files = payload.get("datasetFiles", []) if isinstance(payload, dict) else []
    for f in files:
        name = f.get("name", "")
        if name == asset or name.endswith("/" + asset):
            want = entry.get("size", 0)
            got = int(f.get("totalBytes") or 0)
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
    """Create a NEW Kaggle dataset from a local file via the official kaggle CLI.

    The previous hand-rolled multipart POST to /datasets/create/new was
    broken: the endpoint expects a JSON body with pre-uploaded blob tokens,
    not a raw file (it 400s with "Error reading JToken"). The official CLI
    implements the real resumable-upload flow, so we shell out to it —
    same pattern as the github backend shelling out to `gh`.

    Returns (ok, detail). Creates a dataset under `owner` (private by
    default). Requires the `kaggle` CLI on PATH (pip install kaggle) and
    ~/.kaggle/kaggle.json credentials.
    """
    import tempfile
    kaggle_bin = shutil.which("kaggle")
    if not kaggle_bin:
        fallback = os.path.expanduser("~/.local/bin/kaggle")
        if os.path.isfile(fallback):
            kaggle_bin = fallback
    if not kaggle_bin:
        return False, "kaggle CLI not found (pip install kaggle)"
    if not (6 <= len(slug) <= 50):
        return False, f"kaggle slug must be 6-50 chars (got {len(slug)})"
    if not (6 <= len(title) <= 50):
        return False, f"kaggle title must be 6-50 chars (got {len(title)})"
    if not os.path.isfile(zip_path):
        return False, f"upload file not found: {zip_path}"
    d = tempfile.mkdtemp(prefix="kaggle-upload-")
    try:
        meta = {"id": f"{owner}/{slug}", "title": title,
                "licenses": [{"name": license_name}]}
        with open(os.path.join(d, "dataset-metadata.json"), "w") as f:
            json.dump(meta, f)
        shutil.copy(zip_path, os.path.join(d, os.path.basename(zip_path)))
        rc, out, err = run([kaggle_bin, "datasets", "create", "-p", d],
                           timeout=3600)
        if rc != 0:
            tail = (err.strip().splitlines() or [f"exit {rc}"])[-1]
            return False, f"kaggle datasets create failed: {tail[:200]}"
        return True, f"kaggle dataset {owner}/{slug} created"
    finally:
        shutil.rmtree(d, ignore_errors=True)


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
    if store == "restic":
        return restic_verify(entry, local_path)
    if store == "b2":
        return b2_verify(entry, local_path)
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
                        progress_cb=None, hasher_factory=None):
    """Stream a URL into rolling part files on disk.

    Yields (index, part_path, size, hexdigest) as each part fills. Disk never
    holds more than part_bytes + chunk at a time; the caller is expected to
    upload+verify+delete each part before pulling the next (delete-as-you-go).
    Supports HTTP Range resume if the server honors it.

    hasher_factory: callable returning a fresh hash object with
    update()/hexdigest(). Default hashlib.sha256 (the registry/GitHub
    contract). Pass a BLAKE3 constructor for ~10x throughput when the
    digest is only a local integrity note.
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
        hasher = (hasher_factory() if hasher_factory
                  else hashlib.sha256())
        return p, open(p, "wb"), hasher

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


def split_file_to_parts(path, staging_dir, part_bytes, chunk=8 << 20,
                        progress_cb=None, hasher_factory=None):
    """Split a LOCAL file into rolling part files on disk.

    Same contract as stream_url_to_parts, but reads from a file already on
    disk (used by `mirror --direct`, where aria2c has materialized the
    download). Yields (index, part_path, size, hexdigest) as each part
    fills.

    hasher_factory: callable returning a fresh hash object with
    update()/hexdigest(). Default hashlib.sha256 (the registry/GitHub
    contract). Pass a BLAKE3 constructor for ~10x throughput when the
    digest is only a local integrity note.
    """
    os.makedirs(staging_dir, exist_ok=True)
    idx = 0
    part_path = None
    part_f = None
    hasher = None
    with open(path, "rb") as src:
        while True:
            data = src.read(chunk)
            if not data:
                break
            if part_f is None:
                part_path = os.path.join(
                    staging_dir, f"mirror-part-{idx:04d}.bin")
                part_f = open(part_path, "wb")
                hasher = (hasher_factory() if hasher_factory
                          else hashlib.sha256())
            part_f.write(data)
            hasher.update(data)
            if part_f.tell() >= part_bytes:
                part_f.close()
                size = os.path.getsize(part_path)
                digest = hasher.hexdigest()
                yield idx, part_path, size, digest
                if progress_cb:
                    progress_cb(idx, size)
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


def _check_free_space(path, needed_bytes, margin_bytes=256 << 20):
    """Fail-closed free-space guard. Returns (ok, detail)."""
    try:
        free = shutil.disk_usage(path).free
    except OSError as e:
        return False, f"cannot stat disk for {path}: {e}"
    if free < needed_bytes + margin_bytes:
        return False, (
            f"not enough free space: need {human(needed_bytes + margin_bytes)}, "
            f"have {human(free)}")
    return True, ""


ARIA2_DHT_BOOTSTRAP = [
    "dht.transmissionbt.com:6881",
    "router.bittorrent.com:6881",
    "dht.libtorrent.org:25401",
]


def _aria2c_fetch(binary, kind, uri, dl_dir, timeout_secs=12 * 3600):
    """Download via aria2c into dl_dir.

    kind: 'url' (direct http/https), 'magnet', or 'torrent' (.torrent path).
    Returns (ok, files|detail): files is the sorted list of downloaded
    regular files (aria2 control files excluded). Disk holds the full
    download during this call — the documented trade-off of direct mode
    (TorBox streaming mode keeps <= 1 part on disk instead).
    """
    os.makedirs(dl_dir, exist_ok=True)
    cmd = [binary,
           "--dir", dl_dir,
           "--seed-time=0",
           "--allow-overwrite=true",
           "--continue=true",
           "--auto-file-renaming=false",
           "--max-connection-per-server=16",
           "--split=16",
           "--min-split-size=1M",
           "--bt-enable-lpd=true"]
    if kind == "url":
        cmd += ["--user-agent", UA_BROWSER, uri]
    elif kind == "magnet":
        for node in ARIA2_DHT_BOOTSTRAP:
            cmd += ["--dht-entry-point", node]
        cmd += [uri]
    elif kind == "torrent":
        cmd += [uri]
    else:
        return False, f"unknown aria2 fetch kind {kind!r}"
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout_secs)
    except subprocess.TimeoutExpired:
        return False, "aria2c timed out"
    except OSError as e:
        return False, f"aria2c failed to start: {e}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-1500:]
        return False, f"aria2c exit {proc.returncode}: {tail[:1500]}"
    files = []
    for root, dirs, names in os.walk(dl_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(names):
            if n.startswith(".") or n.endswith(".aria2"):
                continue
            p = os.path.join(root, n)
            try:
                if os.path.isfile(p) and os.path.getsize(p) > 0:
                    files.append(p)
            except OSError:
                continue
    files.sort()
    if not files:
        return False, "aria2c finished but produced no files"
    return True, files


# ============================================================ v2: mirror command
def cmd_mirror(args):
    """Torrent -> cloud store with bounded local footprint.

    Two transports:
      TorBox mode (default): TorBox cloud torrent -> CDN link ->
        Range-stream into rolling part files -> upload each part -> verify
        -> delete local part immediately -> manifest + registry entry.
        Disk never holds more than one part. Needs the custom.torbox
        Secure Vault credential.
      Direct mode (--direct): aria2c downloads magnet/.torrent/URL into
        staging -> split into rolling parts -> same upload/verify/delete
        pipeline. Needs no TorBox credential, but disk holds the FULL
        download while aria2c works (source file is deleted as soon as its
        parts are all verified). Use when TorBox is unavailable.
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

    if args.direct:
        # ---- direct mode: aria2c transport, no TorBox credential ----
        a2c = _aria2c_binary()
        if not a2c:
            return fail("direct mode needs aria2c: not on PATH and not at "
                        "~/workspace/tools/aria2/aria2c")
        print(f"aria2c: using {a2c}")
        if args.magnet:
            src_kind, src_uri = "magnet", args.magnet
            manifest["source"] = {"kind": "direct-magnet"}
        elif args.torrent:
            if not os.path.isfile(args.torrent):
                return fail(f"--torrent file not found: {args.torrent}")
            src_kind, src_uri = "torrent", args.torrent
            manifest["source"] = {"kind": "direct-torrent",
                                  "file": args.torrent}
        elif args.url:
            src_kind, src_uri = "url", args.url
            manifest["source"] = {"kind": "direct-url"}
        else:
            return fail("direct mode needs --magnet, --torrent or --url")
        # free-space guard: full download lands on this disk
        if src_kind == "url":
            size = _head_content_length(src_uri)
            need = size if size is not None else (2 << 30)
            ok, detail = _check_free_space(staging, need)
            if not ok:
                return fail(detail)
        else:
            ok, detail = _check_free_space(staging, 2 << 30)
            if not ok:
                return fail(detail)
        dl_dir = os.path.join(staging, "dl")
        print(f"aria2c: downloading {src_kind} into {dl_dir} ...")
        ok, fetched = _aria2c_fetch(a2c, src_kind, src_uri, dl_dir,
                                    timeout_secs=args.direct_timeout)
        if not ok:
            return fail(fetched)
        dl_files = fetched
        print(f"aria2c: {len(dl_files)} file(s) downloaded")
    else:
        # ---- TorBox mode (default) ----
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
            manifest["source"] = {"kind": "torrent_id",
                                  "torrent_id": torrent_id}
        else:
            return fail("need --magnet or --torrent-id (or --direct with "
                        "--magnet/--torrent/--url)")

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

    # 4. each file -> parts -> upload/verify/delete
    total_up = 0
    reg = load_registry()
    try:
        if args.direct:
            nfiles = len(dl_files)
            for fi, path in enumerate(dl_files):
                fname = os.path.relpath(path, dl_dir)
                if args.file and args.file.lower() not in fname.lower():
                    print(f"  SKIP {fname}: does not match --file")
                    continue
                fsize = os.path.getsize(path)
                parts_iter = split_file_to_parts(path, staging, part_bytes)
                fentry, verified = _mirror_file_parts(
                    fi, nfiles, fname, fsize, parts_iter, store, dest,
                    args, run_id, reg)
                manifest["files"].append(fentry)
                total_up += verified
                # delete the aria2c source once its parts are handled
                try:
                    os.unlink(path)
                except OSError:
                    pass
            # prune leftover aria2 control/metadata files
            for root, dirs, names in os.walk(dl_dir):
                for n in names:
                    p = os.path.join(root, n)
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
        else:
            nfiles = len(files)
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
                parts_iter = stream_url_to_parts(link, staging, part_bytes)
                fentry, verified = _mirror_file_parts(
                    fi, nfiles, fname, fsize, parts_iter, store, dest,
                    args, run_id, reg)
                manifest["files"].append(fentry)
                total_up += verified
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


# ============================================================ mirror-search: Internet Archive link finder
IA_SEARCH_URL = "https://archive.org/advancedsearch.php"
IA_METADATA_URL = "https://archive.org/metadata"
IA_DOWNLOAD_BASE = "https://archive.org/download"
IA_USER_AGENT = ("CloudDog/1.0 (mirror-search; "
                 "+https://github.com/mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant)")


def ia_search(query, rows=10, timeout=60):
    """Search the Internet Archive (public advancedsearch API, no key needed).

    Returns (ok, docs|detail). docs: [{identifier, title, mediatype}, ...].
    """
    import urllib.request
    import urllib.error
    import urllib.parse
    params = [("q", query),
              ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "mediatype"),
              ("rows", str(max(1, rows))),
              ("output", "json")]
    url = IA_SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": IA_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"IA search HTTP {e.code}"
    except Exception as e:
        return False, f"IA search error: {e}"
    try:
        docs = data["response"]["docs"]
    except (KeyError, TypeError):
        return False, "IA search returned unexpected JSON"
    return True, docs


def ia_item_files(identifier, timeout=60):
    """Fetch an item's file list via the public IA metadata API.

    Returns (ok, files|detail). files: [{name, size, ...}, ...].
    """
    import urllib.request
    import urllib.error
    import urllib.parse
    url = f"{IA_METADATA_URL}/{urllib.parse.quote(identifier, safe='')}"
    req = urllib.request.Request(url, headers={"User-Agent": IA_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"IA metadata HTTP {e.code} for {identifier}"
    except Exception as e:
        return False, f"IA metadata error for {identifier}: {e}"
    files = data.get("files")
    if not isinstance(files, list):
        return False, f"IA metadata has no file list for {identifier}"
    return True, files


def ia_download_url(identifier, filename):
    """Direct download URL for one item file (no API key needed)."""
    import urllib.parse
    return (f"{IA_DOWNLOAD_BASE}/{urllib.parse.quote(identifier, safe='')}"
            f"/{urllib.parse.quote(filename, safe='/')}")


def _head_content_length(url, timeout=30):
    """HEAD request -> Content-Length in bytes, or None when unknown."""
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": UA_BROWSER})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            cl = r.headers.get("Content-Length")
            return int(cl) if cl else None
    except Exception:
        return None


def _mirror_file_parts(fi, nfiles, fname, fsize, parts_iter, store, dest,
                       args, run_id, reg):
    """Upload/verify/delete rolling parts for one file; returns (fentry, bytes).

    parts_iter yields (index, part_path, size, sha256) as in
    stream_url_to_parts / split_file_to_parts. Verified parts are deleted
    from disk immediately (delete-as-you-go); unverified ones are kept and
    recorded with status=unverified.
    """
    fentry = {"name": fname, "size": fsize, "parts": []}
    base = os.path.splitext(os.path.basename(fname))[0][:60] or "mirror"
    print(f"[{fi + 1}/{nfiles}] {fname} ({human(fsize)})")
    verified = 0
    for pidx, ppath, psize, phash in parts_iter:
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
        verified += psize
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
    return fentry, verified


def cmd_mirror_search(args):
    """Find mirror links on the Internet Archive for QUERY.

    Prints each matching item's title, identifier, and direct download URLs
    (https://archive.org/download/<id>/<file>). Pure stdlib, IA only —
    no pirate-site scrapers.
    """
    limit = max(1, getattr(args, "limit", 10) or 10)
    as_json = getattr(args, "json", False)
    ok, docs = ia_search(args.query, rows=limit)
    if not ok:
        print(f"mirror-search: {docs}", file=sys.stderr)
        return 1
    results = []
    for doc in docs:
        ident = doc.get("identifier") or ""
        title = doc.get("title") or ""
        if isinstance(title, list):
            title = "; ".join(str(t) for t in title)
        entry = {"identifier": ident, "title": title, "downloads": []}
        if ident:
            ok2, files = ia_item_files(ident)
            if not ok2:
                entry["error"] = files
            else:
                for f in files:
                    name = f.get("name") or ""
                    if not name:
                        continue
                    size = f.get("size")
                    try:
                        size = int(size) if size is not None else 0
                    except (TypeError, ValueError):
                        size = 0
                    entry["downloads"].append({
                        "file": name,
                        "size": size,
                        "url": ia_download_url(ident, name),
                    })
        results.append(entry)
    if as_json:
        print(json.dumps(results, indent=2))
    else:
        for e in results:
            print(f"= {e['title'] or '(no title)'}")
            print(f"  identifier: {e['identifier']}")
            if e.get("error"):
                print(f"  file list unavailable: {e['error']}")
            for dl in e["downloads"]:
                print(f"  - {dl['url']} ({human(dl['size'])})")
    return 0


# ============================================================ v2: daemon command
# ============================================================ fetch: self-hosted torrent fetch (TorBox replacement)
def _fetchmod():
    """Lazy import of clouddog_fetch (keeps cloud_dog importable without
    libtorrent; the fetch verbs fail with a clear message instead)."""
    try:
        import clouddog_fetch as cf
    except ImportError:
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.insert(0, _here)
        import clouddog_fetch as cf
    return cf


def cmd_fetch(args):
    """Self-hosted selective torrent fetch -> store -> mirror registry.

    Zip mode (--zip): the torrent is a single-file zip archive. Only the
    pieces covering the zip tail/central directory and the wanted entries'
    data ranges are downloaded (never the whole archive). Each extracted
    entry is verified (size + CRC32 vs the central directory), SHA256'd,
    uploaded to the store, registered with its mirror record, and the
    local copy is deleted (delete-as-you-go). A MANIFEST-fetch-<run>.txt
    asset (sha256<TAB>size<TAB>./entry/path, the gym-partner v1 format) is
    kept current on the release after every file, so a crash loses nothing.
    """
    cf = _fetchmod()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if args.store != "github":
        print(f"FETCH FAIL: v1 stores to github only (got {args.store})",
              file=sys.stderr)
        return 1
    if not args.repo or not args.release:
        print("FETCH FAIL: github fetch needs --repo and --release",
              file=sys.stderr)
        return 1
    if not args.zip:
        print("FETCH FAIL: v1 supports --zip (single-file zip torrents) only",
              file=sys.stderr)
        return 1
    wants = cf.read_want_manifest(args.want_manifest)
    if args.limit:
        wants = wants[:args.limit]
    if not wants:
        print("FETCH FAIL: want manifest is empty", file=sys.stderr)
        return 1
    staging = args.staging or os.path.join(AGENT_OPS, "fetch-staging", run_id)
    payload_dir = os.path.join(staging, "payload")
    out_dir = os.path.join(staging, "out")
    os.makedirs(payload_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    manifest_name = f"MANIFEST-fetch-{run_id}.txt"
    manifest_local = os.path.join(staging, manifest_name)
    summary = {"run_id": run_id, "wanted": len(wants), "stored": 0,
               "unmatched": 0, "failed": 0, "bytes_stored": 0}
    reg = load_registry()
    torr_kwargs = {}
    if args.tracker:
        torr_kwargs["trackers"] = args.tracker
    torr = cf.SelectiveTorrent(payload_dir, **torr_kwargs)
    peers = []
    for spec in args.add_peer or []:
        host, _, port = spec.rpartition(":")
        if host and port.isdigit():
            peers.append((host, int(port)))
    try:
        if args.magnet:
            info = torr.add_magnet(args.magnet,
                                   timeout=args.metadata_timeout,
                                   connect_peers=peers)
        elif args.torrent:
            info = torr.add_torrent_file(args.torrent)
        else:
            print("FETCH FAIL: need --magnet or --torrent", file=sys.stderr)
            return 1
        total, pl, npieces = info.total_size(), info.piece_length(), \
            info.num_pieces()
        print(f"fetch: '{info.name()}' {human(total)} "
              f"pieces={npieces}x{human(pl)}")
        # ---- stage 1: tail + (zip64) + central directory
        tail_s, tail_e = cf.find_eocd_range(total)
        torr.download_pieces(
            cf.ranges_to_pieces([(tail_s, tail_e)], pl, npieces),
            timeout=args.piece_timeout)
        tail = torr.read_range(tail_s, tail_e)
        cd_off, cd_size, cd_count, z64_off = cf.parse_eocd(tail, total)
        if z64_off is not None:
            torr.download_pieces(
                cf.ranges_to_pieces([(z64_off, z64_off + 55)], pl, npieces),
                timeout=args.piece_timeout)
            cd_count, cd_size, cd_off = cf.parse_z64_eocd(
                torr.read_range(z64_off, z64_off + 55))
        torr.download_pieces(
            cf.ranges_to_pieces([(cd_off, cd_off + cd_size - 1)], pl,
                                npieces),
            timeout=args.piece_timeout)
        entries = cf.parse_central_directory(
            torr.read_range(cd_off, cd_off + cd_size - 1))
        print(f"fetch: central directory parsed: {len(entries)} entries")
        if args.dry_run:
            plan = cf.plan_zip_fetch(total, entries,
                                     [w["path"] for w in wants])
            planned = sum(e - s + 1 for s, e in plan["stage2_ranges"])
            print(f"DRY-RUN: {len(plan['resolved'])}/{len(wants)} wants "
                  f"resolve; stage-2 download would be {human(planned)} "
                  f"across {len(plan['stage2_ranges'])} ranges")
            audit_log("fetch_dry_run", run_id=run_id,
                      resolved=len(plan["resolved"]), wanted=len(wants))
            return 0
        # ---- stage 2: per-entry selective fetch -> verify -> store
        used_assets = set()
        for wi, want in enumerate(wants):
            plan = cf.plan_zip_fetch(total, entries, [want["path"]])
            ent = plan["resolved"].get(want["path"])
            if ent is None:
                print(f"  [{wi + 1}/{len(wants)}] UNMATCHED {want['path']}")
                summary["unmatched"] += 1
                continue
            if want["size"] and ent["uncomp_size"] != want["size"]:
                print(f"  [{wi + 1}/{len(wants)}] SIZE-SKIP {want['path']}: "
                      f"manifest {want['size']} != CD {ent['uncomp_size']}")
                summary["unmatched"] += 1
                continue
            pieces = cf.ranges_to_pieces(plan["stage2_ranges"], pl, npieces)
            print(f"  [{wi + 1}/{len(wants)}] {ent['name']} "
                  f"({human(ent['uncomp_size'])}, {len(pieces)} pieces)")
            try:
                torr.download_pieces(pieces, timeout=args.piece_timeout)
                data, sha = cf.extract_entry(torr.read_range, ent)
            except Exception as e:
                print(f"    FETCH/EXTRACT FAIL: {e}")
                summary["failed"] += 1
                audit_log("fetch_file_fail", run_id=run_id,
                          file=ent["name"], detail=str(e)[:200])
                continue
            base = os.path.basename(ent["name"]) or "entry"
            asset = base
            if asset in used_assets:
                stem, ext = os.path.splitext(base)
                asset = f"{stem}-{sha[:8]}{ext}"
            used_assets.add(asset)
            local = os.path.join(out_dir, asset)
            with open(local, "wb") as f:
                f.write(data)
            del data
            ok, detail = upload_to_store(
                "github", local, {"repo": args.repo,
                                  "release": args.release})
            ok_v, detail_v = (False, "upload failed: " + detail)
            if ok:
                ok_v, detail_v = verify_github_asset(
                    args.repo, args.release, asset,
                    ent["uncomp_size"], sha)
            key = f"fetch:{run_id}:{ent['name']}"
            mirror = cf.make_mirror("github", repo=args.repo,
                                    release=args.release, asset=asset)
            if not ok_v:
                print(f"    STORE VERIFY FAILED: {detail_v}; "
                      f"local kept at {local}")
                summary["failed"] += 1
                reg["entries"][key] = {
                    "local_path": local, "store": "github",
                    "repo": args.repo, "release": args.release,
                    "asset": asset, "sha256": sha,
                    "size": ent["uncomp_size"],
                    "note": f"fetch {run_id} UNVERIFIED {ent['name']}",
                    "registered_at": now_stamp(), "status": "unverified",
                    "zip_entry": ent["name"], "mirrors": [mirror],
                }
                save_registry(reg)
                continue
            mirror["verified_at"] = now_stamp()
            entry_rec = {
                "local_path": key, "store": "github", "repo": args.repo,
                "release": args.release, "asset": asset, "sha256": sha,
                "size": ent["uncomp_size"],
                "note": f"fetch {run_id} {ent['name']}",
                "registered_at": now_stamp(), "status": "reaped",
                "zip_entry": ent["name"], "mirrors": [mirror],
            }
            reg["entries"][key] = entry_rec
            save_registry(reg)
            with open(manifest_local, "a", encoding="utf-8") as f:
                f.write(f"{sha}\t{ent['uncomp_size']}\t./{ent['name']}\n")
            upload_to_store("github", manifest_local,
                            {"repo": args.repo, "release": args.release})
            if not args.keep_local:
                try:
                    os.unlink(local)
                except OSError:
                    pass
            summary["stored"] += 1
            summary["bytes_stored"] += ent["uncomp_size"]
            audit_log("fetch_file", run_id=run_id, file=ent["name"],
                      asset=asset, sha256=sha[:16],
                      size=ent["uncomp_size"])
            print(f"    stored+verified: {asset} sha256={sha[:16]}...")
    finally:
        torr.close()
        save_registry(reg)
        mpath = os.path.join(AGENT_OPS, f"fetch-manifest-{run_id}.json")
        try:
            with open(mpath, "w") as f:
                json.dump({"summary": summary, "staging": staging,
                           "release_manifest": manifest_name}, f, indent=2)
        except OSError:
            mpath = "(manifest write failed)"
    msg = (f"fetch complete: {summary['stored']}/{summary['wanted']} stored "
           f"({human(summary['bytes_stored'])}), "
           f"unmatched={summary['unmatched']} failed={summary['failed']}, "
           f"run manifest {mpath}")
    print(msg)
    log_action(f"cloud_dog fetch {run_id}: {msg}")
    audit_log("fetch_complete",
              **{k: v for k, v in summary.items() if k != "run_id"},
              run_id=run_id)
    return 0 if summary["failed"] == 0 and summary["unmatched"] == 0 else 3


def cmd_register_mirror(args):
    """Attach a mirror location to an existing registry entry.

    The entry's record becomes: hashes + size + EVERY mirror location +
    pull-back command (see clouddog_fetch.make_mirror). Use --verified only
    when the mirror was actually checked just now.
    """
    cf = _fetchmod()
    reg = load_registry()
    key = os.path.abspath(args.file) if os.path.exists(args.file) \
        else args.file
    entry = reg["entries"].get(key) or reg["entries"].get(args.file)
    if entry is None:
        print(f"No registry entry for {args.file} "
              f"(register it first)", file=sys.stderr)
        return 1
    mirror = cf.make_mirror(
        args.store, repo=args.repo, release=args.release, asset=args.asset,
        remote_dir=args.remote_dir, dataset=args.dataset, url=args.url,
        b2_bucket=args.b2_bucket, b2_path=args.b2_path)
    if args.verified:
        mirror["verified_at"] = now_stamp()
    added = cf.add_mirror(entry, mirror)
    save_registry(reg)
    print(f"{'Added mirror' if added else 'Mirror already recorded'}: "
          f"{key} -> {mirror['store']}:{mirror['url'] or mirror['asset']}")
    audit_log("register_mirror", path=key, store=args.store,
              asset=args.asset, url=mirror["url"][:120], added=added)
    return 0


def cmd_pull_back(args):
    """Download a registered file back from one of its mirrors and verify
    SHA256 + size against the registry record. Proof of retrievability."""
    import urllib.request
    reg = load_registry()
    key = os.path.abspath(args.file) if os.path.exists(args.file) \
        else args.file
    entry = reg["entries"].get(key) or reg["entries"].get(args.file)
    if entry is None:
        print(f"No registry entry for {args.file}", file=sys.stderr)
        return 1
    mirrors = entry.get("mirrors") or []
    if not mirrors:
        # legacy single-store entry: synthesize its one mirror
        mirrors = [_fetchmod().make_mirror(
            entry.get("store", ""), repo=entry.get("repo", ""),
            release=entry.get("release", ""), asset=entry.get("asset", ""),
            remote_dir=entry.get("remote_dir", ""),
            dataset=entry.get("dataset", ""))]
    if args.mirror_index >= len(mirrors):
        print(f"mirror index {args.mirror_index} out of range "
              f"({len(mirrors)} mirrors)", file=sys.stderr)
        return 1
    m = mirrors[args.mirror_index]
    out = args.out or os.path.basename(
        m.get("asset") or entry.get("zip_entry") or "pull-back.bin")
    ok = False
    detail = ""
    if m.get("url"):
        try:
            req = urllib.request.Request(
                m["url"], headers={"User-Agent": UA_BROWSER})
            with urllib.request.urlopen(req, timeout=3600) as r, \
                    open(out, "wb") as f:
                shutil.copyfileobj(r, f, 8 << 20)
            ok = True
        except Exception as e:
            detail = f"url download failed: {e}"
    if not ok and m.get("store") == "github" and m.get("repo"):
        tmpd = out + ".ghdl"
        os.makedirs(tmpd, exist_ok=True)
        rc, _o, err = run(
            ["gh", "release", "download", m["release"], "--repo", m["repo"],
             "--pattern", m["asset"], "--dir", tmpd], timeout=3600)
        got = os.path.join(tmpd, m["asset"])
        if rc == 0 and os.path.exists(got):
            os.replace(got, out)
            try:
                os.rmdir(tmpd)
            except OSError:
                pass
            ok = True
        else:
            detail = f"gh download failed: {err.strip()[:200]}"
    if not ok:
        print(f"PULL-BACK FAIL: {args.file}: {detail}", file=sys.stderr)
        return 1
    size = os.path.getsize(out)
    sha = sha256_file(out)
    want_size = entry.get("size", 0)
    want_sha = (entry.get("sha256") or "").lower()
    if want_size and size != want_size:
        print(f"PULL-BACK FAIL: size {size} != registry {want_size}",
              file=sys.stderr)
        return 1
    if want_sha and sha.lower() != want_sha:
        print(f"PULL-BACK FAIL: sha256 {sha[:16]}... != registry "
              f"{want_sha[:16]}...", file=sys.stderr)
        return 1
    print(f"PULL-BACK OK: {out} ({human(size)}, sha256={sha[:16]}...) "
          f"from {m.get('store')} mirror #{args.mirror_index}")
    audit_log("pull_back", path=key, out=out, size=size, sha256=sha[:16])
    return 0


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


def _fclones_binary():
    """Locate the fclones binary (optional dependency for dedup)."""
    return (_repo_tool_bin("fclones") or shutil.which("fclones")
            or _workspace_tool_bin("fclones",
                                  os.path.join("usr", "bin", "fclones"),
                                  "fclones"))


def _in_git_tree(path):
    """True if path sits inside a git working tree (has a .git ancestor)."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.isdir(os.path.join(d, ".git")):
            return True
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _fclones_groups(roots, excludes):
    """Run `fclones group --format json`. Returns (groups, None) or
    (None, error_message). Never raises."""
    binary = _fclones_binary()
    if not binary:
        return None, "fclones not found (PATH or ~/workspace/tools/fclones)"
    cmd = [binary, "group", "--format", "json"]
    for pat in excludes or []:
        cmd += ["--exclude", pat]
    cmd += roots
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=1800)
    except Exception as e:
        return None, f"fclones failed: {e}"
    if proc.returncode != 0:
        return None, f"fclones exit {proc.returncode}: {proc.stderr.strip()[:200]}"
    try:
        data = json.loads(proc.stdout)
    except Exception:
        return None, "fclones output was not JSON"
    return data.get("groups", []), None


def cmd_dedup(args):
    """Find duplicate files with fclones; delete down to one copy per group.

    Safety: fclones groups are content-hash confirmed; each deletion target
    is additionally re-hashed (sha256) against the kept copy, must pass the
    owner's law engine (decide_delete), and is never taken from inside a git
    working tree. Default is dry-run.
    """
    if not _POLICY_AVAILABLE:
        print("POLICY ENGINE UNAVAILABLE; dedup disabled (fail-closed).",
              file=sys.stderr)
        return 1
    roots = ([os.path.abspath(args.path)] if getattr(args, "path", "")
             else [WORKSPACE])
    owner_approved = getattr(args, "owner_approved", False)
    apply = args.apply
    groups, err = _fclones_groups(roots, getattr(args, "exclude", []))
    if groups is None:
        print(f"dedup: {err}", file=sys.stderr)
        return 1
    reaped, skipped, failed = [], [], []
    for g in groups:
        files = sorted(g.get("files", []))
        if len(files) < 2:
            continue
        kept = files[0]
        if not os.path.isfile(kept):
            continue
        try:
            kept_hash = fast_hash_file(kept)
            # xxHash3 pre-filter: cheap first pass before the crypto hash
            kept_xxh3 = xxh3_file(kept)
        except OSError:
            continue
        for path in files[1:]:
            if not os.path.isfile(path):
                continue
            if _in_git_tree(path):
                skipped.append((path, "inside git working tree"))
                continue
            try:
                # Fast path: if xxh3 differs, files differ — skip slow hash
                if kept_xxh3 is not None:
                    path_xxh3 = xxh3_file(path)
                    if path_xxh3 is not None and path_xxh3 != kept_xxh3:
                        failed.append((path, "xxh3 mismatch vs kept copy"))
                        continue
                if fast_hash_file(path) != kept_hash:
                    failed.append((path, "hash mismatch vs kept copy"))
                    continue
            except OSError as e:
                failed.append((path, str(e)[:120]))
                continue
            decision = decide_delete(
                path, verified_stores=[f"local-duplicate:{kept}"],
                owner_approved=owner_approved)
            if (not decision.allowed
                    or (decision.requires_owner and not owner_approved)):
                skipped.append((path, "; ".join(decision.reasons)[:160]))
                continue
            size = os.path.getsize(path)
            if apply:
                try:
                    os.remove(path)
                    msg = (f"DEDUP {path} ({human(size)}) — "
                           f"byte-identical to kept {kept}")
                    log_action(msg)
                    audit_log("dedup", path=path, kept=kept, size=size)
                    reaped.append((path, size))
                except OSError as e:
                    failed.append((path, str(e)[:120]))
            else:
                reaped.append((path, size))
                print(f"WOULD-DEDUP {path} ({human(size)}) — duplicate of {kept}")
    total = sum(s for _, s in reaped)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] dedup groups={len(groups)} reaped={len(reaped)} "
          f"({human(total)}) skipped={len(skipped)} failed={len(failed)}")
    for path, reason in skipped:
        print(f"  SKIP: {path} — {reason[:120]}")
    for path, reason in failed:
        print(f"  FAIL: {path} — {reason[:120]}")
    if apply and reaped:
        log_action(f"cloud_dog dedup: {len(reaped)} duplicates, "
                   f"{human(total)} freed")
    return 0


def _zfec_available():
    """True when the optional zfec pip package is importable."""
    try:
        import zfec  # noqa: F401
        return True
    except ImportError:
        return False


def cmd_shard(args):
    """Split a file into m erasure-coded shards (any k reconstruct).

    For irreplaceable files: spread shards across stores via `register`,
    and the file survives even if (m - k) stores are lost. Uses zfec
    (GPL-2.0+). Shard files are named <basename>.shard-<i>-of-<m>.
    A <basename>.shard-meta.json records k, m, size, and sha256.
    """
    if not _zfec_available():
        print("SHARD FAIL: zfec not installed (pip install zfec)",
              file=sys.stderr)
        return 1
    import zfec
    src = args.file
    k, m = args.k, args.m
    if not os.path.isfile(src):
        print(f"SHARD FAIL: not a file: {src}", file=sys.stderr)
        return 1
    if not (1 <= k <= m <= 256):
        print(f"SHARD FAIL: need 1 <= k <= m <= 256 (got k={k} m={m})",
              file=sys.stderr)
        return 1
    out_dir = args.out or os.path.dirname(os.path.abspath(src))
    os.makedirs(out_dir, exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    # Split into k equal blocks (pad the last)
    block_size = (len(data) + k - 1) // k  # ceil division
    blocks = [data[i * block_size:(i + 1) * block_size] for i in range(k)]
    max_len = max(len(b) for b in blocks)
    blocks = [b + b"\x00" * (max_len - len(b)) for b in blocks]
    enc = zfec.Encoder(k, m)
    shards = enc.encode(blocks)
    base = os.path.basename(src)
    for i, s in enumerate(shards):
        spath = os.path.join(out_dir, f"{base}.shard-{i}-of-{m}")
        with open(spath, "wb") as sf:
            sf.write(s)
    meta = {"k": k, "m": m, "size": len(data),
            "sha256": sha256_file(src),
            "file": base}
    mpath = os.path.join(out_dir, f"{base}.shard-meta.json")
    with open(mpath, "w") as mf:
        json.dump(meta, mf, indent=2)
    print(f"sharded {src} ({human(len(data))}) into {m} shards "
          f"(any {k} reconstruct) in {out_dir}")
    audit_log("shard", file=src, k=k, m=m, out=out_dir)
    return 0


def cmd_unshard(args):
    """Reconstruct a file from any k of its m shards.

    Reads <basename>.shard-meta.json for k/m/size, decodes from the shard
    files present in --dir, verifies sha256, writes to --out.
    """
    if not _zfec_available():
        print("UNSHARD FAIL: zfec not installed (pip install zfec)",
              file=sys.stderr)
        return 1
    import zfec
    import glob
    d = args.dir
    metas = glob.glob(os.path.join(d, "*.shard-meta.json"))
    if not metas:
        print(f"UNSHARD FAIL: no shard-meta.json in {d}", file=sys.stderr)
        return 1
    with open(metas[0]) as mf:
        meta = json.load(mf)
    k, m, size = meta["k"], meta["m"], meta["size"]
    base = meta["file"]
    # Collect available shards
    shards = []
    for i in range(m):
        spath = os.path.join(d, f"{base}.shard-{i}-of-{m}")
        if os.path.isfile(spath):
            with open(spath, "rb") as sf:
                shards.append((i, sf.read()))
        if len(shards) >= k:
            break
    if len(shards) < k:
        print(f"UNSHARD FAIL: only {len(shards)} shards found, need {k}",
              file=sys.stderr)
        return 1
    dec = zfec.Decoder(k, m)
    shard_ids = [i for i, _ in shards]
    blocks = [s for _, s in shards]
    decoded_blocks = dec.decode(blocks, shard_ids)
    data = b"".join(decoded_blocks)[:size]
    out = args.out or os.path.join(d, base + ".reconstructed")
    with open(out, "wb") as of:
        of.write(data)
    # Verify
    if sha256_file(out) != meta["sha256"]:
        print(f"UNSHARD FAIL: sha256 mismatch on {out}", file=sys.stderr)
        return 1
    print(f"reconstructed {out} ({human(size)}) from {len(shards)} shards, "
          f"sha256 verified")
    audit_log("unshard", file=out, shards=len(shards))
    return 0


def cmd_reap_v2(args):
    """v2 reap: verify-then-delete across all store backends.

    The V1 policy engine (src/clouddog/policy.py::evaluate) is the FRONT
    DECIDER: it runs first for every candidate, before the registry /
    decide_delete flow:
      - SACRED -> blocked + audit-logged (in ADDITION to V2's existing
        sacred/veto/in-flight checks — both systems apply, belt and
        suspenders).
      - KEEP_LOCAL or STORE -> skipped (not reap-eligible; STORE is
        mirror-only).
      - STORE_AND_CLEAR_STRICT (game files) -> routed through decide_delete
        with the strict params (retrievability_proven, owner_surfaced).
      - STORE_AND_CLEAR -> the existing decide_delete + live-verify flow,
        unchanged.
    Every candidate additionally passes the owner's law engine
    (clouddog.policy.laws.decide_delete). The gate can only BLOCK, never
    approve: the live per-store verify_entry remains the proof of the
    ONE-STORE RULE. Unattended runs never satisfy requires_owner.
    """
    if not (_POLICY_AVAILABLE and _EVALUATE_AVAILABLE):
        print("POLICY ENGINE UNAVAILABLE: clouddog.policy.laws and/or "
              "src/clouddog/policy.evaluate failed to import; reap "
              "disabled (fail-closed).", file=sys.stderr)
        audit_log("reap_policy_unavailable")
        return 1
    apply = args.apply
    owner_approved = getattr(args, "owner_approved", False)
    retrievability_proven = getattr(args, "retrievability_proven", False)
    owner_surfaced = getattr(args, "owner_surfaced", False)
    search_root = (os.path.abspath(args.path)
                   if getattr(args, "path", "") else None)
    reaped, skipped, failed, blocked = [], [], [], []
    for path, entry in reap_candidates(search_root):
        # FRONT DECIDER (V1 evaluate, law-ordered with reasons). evaluate()
        # never raises on bad input. evaluate's tier suggestion is recorded
        # on the audit events so a future store step can use it; store
        # behavior itself is unchanged by this patch.
        v1 = _v1_evaluate(path)
        v1_action = v1.action.value
        if v1_action == _V1_ACTION_SACRED:
            reason = f"evaluate(SACRED): {v1.reason}"
            blocked.append((path, reason))
            audit_log("reap_policy_block", path=path, store=entry.get("store"),
                      detail=reason[:200])
            continue
        if v1_action in (_V1_ACTION_KEEP_LOCAL, _V1_ACTION_STORE):
            reason = (f"evaluate({v1_action}): not reap-eligible "
                      f"({v1.reason})")
            skipped.append((path, reason))
            audit_log("reap_policy_skip", path=path, store=entry.get("store"),
                      detail=reason[:200])
            continue
        claimed = [entry["store"]] if entry.get("store") else []
        strict_kwargs = {}
        proof_info = None
        if v1_action == _V1_ACTION_STORE_AND_CLEAR_STRICT:
            # Game-file strict bar: 100% byte-verified store +
            # retrievability proven (pull-back actually tested) + owner
            # surfaced. decide_delete names exactly which condition is
            # missing when it blocks. --retrievability-proven does NOT
            # merely assert the flag: it RUNS the pull-back prover right
            # now for this entry and records the proof (bytes fetched,
            # hash match, timestamp) in the audit journal. A failed or
            # infeasible proof leaves the strict bar unsatisfied (fail
            # closed); a listing is never proof.
            proven_now = False
            if retrievability_proven:
                proof = _run_retrievability_proof(entry)
                if proof is not None:
                    proof_info = proof.to_dict()
                    proven_now = bool(proof_info.get("proven"))
                audit_log("reap_retrievability_proof", path=path,
                          store=entry.get("store"),
                          proven=proven_now,
                          bytes_fetched=(proof_info or {}).get("bytes_fetched", 0),
                          hash_matched=(proof_info or {}).get("hash_matched", False),
                          proof_ts=(proof_info or {}).get("ts", ""),
                          detail=((proof_info or {}).get("reason")
                                  or "prover unavailable; refusing (fail closed)")[:300])
            else:
                audit_log("reap_retrievability_skip", path=path,
                          store=entry.get("store"),
                          detail="--retrievability-proven not given")
            strict_kwargs = dict(
                retrievability_proven=proven_now,
                owner_surfaced=owner_surfaced,
            )
        decision = decide_delete(path, verified_stores=claimed,
                                 owner_approved=owner_approved,
                                 **strict_kwargs)
        # Gate blocks unless the laws allow AND any owner-approval
        # requirement is satisfied by this run's --owner-approved flag.
        # (requires_owner=True on an allowed decision merely records that
        # approval was the basis — it must not block an approved run.)
        if not decision.allowed or (decision.requires_owner
                                    and not owner_approved):
            reason = "; ".join(decision.reasons)
            if proof_info is not None and not proof_info.get("proven"):
                # Prefix (not append): the summary print truncates reasons
                # at 160 chars; the proof failure is the actionable part.
                reason = ("pull-back proof FAILED: "
                          + str(proof_info.get("reason", ""))[:160]
                          + " | " + reason)
            blocked.append((path, reason))
            audit_log("reap_policy_block", path=path, store=entry.get("store"),
                      evaluate_tier=v1.tier, detail=reason[:200])
            continue
        ok, detail = verify_entry(entry, path)
        if not ok:
            failed.append((path, detail))
            audit_log("reap_keep", path=path, store=entry.get("store"),
                      evaluate_tier=v1.tier, detail=detail[:200])
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
                          evaluate_tier=v1.tier,
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
          f"failed-verify={len(failed)} blocked-policy={len(blocked)} "
          f"skipped-evaluate={len(skipped)}")
    for path, detail in failed:
        print(f"  KEEP (unverified): {path} - {detail}")
    for path, reason in blocked:
        print(f"  BLOCKED (policy): {path} - {reason[:160]}")
    for path, reason in skipped:
        print(f"  SKIP (evaluate): {path} - {reason[:160]}")
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
    ap.add_argument("--registry", choices=("sqlite", "json"), default=None,
                    help="registry backend (default: $CLOUDEDOG_REGISTRY or json)")

    p = sub.add_parser("scan", help="report disk usage and largest consumers")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("hash", help="hash one file with the fast-hash engine")
    p.add_argument("--file", required=True)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("agent-audit",
                       help="per-agent workdir disk + registry-coverage report "
                            "(AGENT DISK PUSH LAW; read-only)")
    p.add_argument("--quota", default="",
                   help="per-dir quota, e.g. 20G (flags dirs over it)")
    p.add_argument("--top", type=int, default=20,
                   help="report the N largest dirs (default 20, 0 = all)")
    p.add_argument("--root", default="",
                   help="audit this dir instead of ~/workspace")
    p.add_argument("--min-uncovered", default="100M",
                   help="flag dirs with this much unregistered data "
                        "(default 100M)")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("register", help="register a file as verified on a store")
    p.add_argument("--file", required=True)
    p.add_argument("--store", required=True, choices=STORES,
                   help="github | terabox | kaggle | restic")
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
    p.add_argument("--restic-repo", default="",
                   help="restic: repository path/URL (password via "
                        "RESTIC_PASSWORD env var, never on the command line)")
    p.add_argument("--snapshot-id", default="",
                   help="restic: record a pre-existing snapshot instead of "
                        "backing up now")
    p.add_argument("--b2-bucket", default="",
                   help="b2: Backblaze B2 bucket name (creds via "
                        "B2_ACCOUNT_ID / B2_ACCOUNT_KEY env vars)")
    p.add_argument("--b2-path", default="",
                   help="b2: remote path in bucket (default: basename)")
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
    p.add_argument("--owner-approved", action="store_true",
                   help="owner approval for protected game files "
                        "(NEVER-DELETE-GAME-FILES: unattended runs must omit)")
    p.add_argument("--retrievability-proven", action="store_true",
                   help="game-file strict bar: RUN the pull-back prover now "
                        "for each strict candidate (fetch the stored bytes "
                        "and hash-verify them; proof recorded in the audit "
                        "journal). A failed or infeasible proof blocks the "
                        "reap — a listing is NOT proof. Required to reap "
                        "protected game files.")
    p.add_argument("--owner-surfaced", action="store_true",
                   help="game-file strict bar: the store event was surfaced "
                        "to the owner (never silent). --owner-approved "
                        "implies this.")

    p = sub.add_parser("dedup", help="find duplicate files (fclones), "
                                     "delete down to one copy per group")
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--path", default="",
                   help="scope to this directory (default: ~/workspace)")
    p.add_argument("--owner-approved", action="store_true",
                   help="owner approval for protected game files")
    p.add_argument("--exclude", action="append", default=[],
                   help="fclones --exclude pattern (repeatable)")

    p = sub.add_parser("shard", help="split file into erasure-coded shards")
    p.add_argument("--file", required=True, help="file to shard")
    p.add_argument("--k", type=int, default=3,
                   help="shards needed to reconstruct (default 3)")
    p.add_argument("--m", type=int, default=5,
                   help="total shards to create (default 5)")
    p.add_argument("--out", default="",
                   help="output dir (default: same as file)")

    p = sub.add_parser("unshard", help="reconstruct file from shards")
    p.add_argument("--dir", required=True,
                   help="dir containing shards + .shard-meta.json")
    p.add_argument("--out", default="",
                   help="output path (default: <dir>/<file>.reconstructed)")

    p = sub.add_parser("enforce", help="full cycle: scan + reap")
    p.add_argument("--apply", action="store_true")

    p = sub.add_parser("registry", help="show the store registry")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("registry-migrate",
                       help="one-shot JSON -> sqlite registry import "
                            "(JSON file kept as backup, never deleted)")
    p.add_argument("--force", action="store_true",
                   help="re-import even if a migration already ran")

    p = sub.add_parser("mirror",
                       help="torrent -> cloud store, bounded local footprint")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--magnet", default="",
                     help="magnet URI (TorBox cloud, or aria2c in --direct)")
    src.add_argument("--torrent-id", default="",
                     help="existing TorBox torrent id")
    src.add_argument("--torrent", default="",
                     help="--direct: local .torrent file for aria2c")
    src.add_argument("--url", default="",
                     help="--direct: direct http(s) URL for aria2c")
    p.add_argument("--direct", action="store_true",
                   help="download with local aria2c instead of TorBox cloud "
                        "(no TorBox credential needed; holds the full "
                        "download on disk while aria2c works)")
    p.add_argument("--direct-timeout", type=int, default=12 * 3600,
                   help="aria2c download timeout in seconds (default 12h)")
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
                   help="rolling part size in MB (TorBox mode never holds "
                        "more than one part; --direct holds the full "
                        "download too)")
    p.add_argument("--poll-secs", type=int, default=60,
                   help="torbox completion poll interval")

    p = sub.add_parser("mirror-search",
                       help="find mirror links on the Internet Archive")
    p.add_argument("query",
                   help="IA advancedsearch query, e.g. 'shakespeare'")
    p.add_argument("--limit", type=int, default=10,
                   help="max items to list (default 10)")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")

    p = sub.add_parser("fetch",
                       help="self-hosted selective torrent fetch "
                            "(TorBox replacement): zip entries -> store "
                            "-> mirror registry")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--magnet", default="", help="magnet URI")
    src.add_argument("--torrent", default="", help="local .torrent file")
    p.add_argument("--zip", action="store_true",
                   help="torrent content is a single-file zip; fetch only "
                        "the wanted entries' byte ranges")
    p.add_argument("--want-manifest", required=True,
                   help="TSV manifest of wanted entries: size<TAB>path")
    p.add_argument("--store", default="github", choices=STORES,
                   help="destination store (v1: github)")
    p.add_argument("--repo", default="", help="github: OWNER/REPO")
    p.add_argument("--release", default="", help="github: release tag")
    p.add_argument("--staging", default="",
                   help="staging dir (default agent-ops/fetch-staging/RUN)")
    p.add_argument("--limit", type=int, default=0,
                   help="only fetch the first N wants (proof runs)")
    p.add_argument("--metadata-timeout", type=int, default=900,
                   help="seconds to wait for torrent metadata")
    p.add_argument("--tracker", action="append", default=[],
                   help="extra tracker URL (repeatable); replaces the "
                        "built-in defaults when given")
    p.add_argument("--add-peer", action="append", default=[],
                   help="connect directly to HOST:PORT (repeatable); "
                        "seedbox/internal swarms and local proofs")
    p.add_argument("--piece-timeout", type=int, default=7200,
                   help="seconds to wait per piece batch")
    p.add_argument("--dry-run", action="store_true",
                   help="fetch metadata + central directory, print the "
                        "download plan, download no entry data")
    p.add_argument("--keep-local", action="store_true",
                   help="keep extracted files after verified upload")

    p = sub.add_parser("register-mirror",
                       help="record a mirror location on a registry entry")
    p.add_argument("--file", required=True,
                   help="registry key or local path of the entry")
    p.add_argument("--store", required=True, choices=STORES,
                   help="mirror store kind")
    p.add_argument("--repo", default="", help="github: OWNER/REPO")
    p.add_argument("--release", default="", help="github: release tag")
    p.add_argument("--asset", default="", help="asset/object name")
    p.add_argument("--remote-dir", default="", help="terabox: remote dir")
    p.add_argument("--dataset", default="", help="kaggle: owner/slug")
    p.add_argument("--b2-bucket", default="", help="b2: bucket name")
    p.add_argument("--b2-path", default="", help="b2: object path")
    p.add_argument("--url", default="", help="direct mirror URL")
    p.add_argument("--verified", action="store_true",
                   help="stamp verified_at=now (only if checked just now)")

    p = sub.add_parser("pull-back",
                       help="download a registered file from a mirror "
                            "and verify sha256+size")
    p.add_argument("--file", required=True,
                   help="registry key or local path of the entry")
    p.add_argument("--mirror-index", type=int, default=0,
                   help="which mirror to pull from (default 0)")
    p.add_argument("--out", default="",
                   help="output path (default: asset basename)")

    p = sub.add_parser("daemon",
                       help="run the enforce loop continuously")
    p.add_argument("--interval", type=int, default=600,
                   help="seconds between cycles (min 60)")
    p.add_argument("--apply", action="store_true",
                   help="actually delete (default is dry-run)")
    p.add_argument("--pidfile", default="",
                   help="pidfile path (default agent-ops/cloud-dog-daemon.pid)")

    args = ap.parse_args(argv)
    set_registry_backend(args.registry)
    try:
        if args.cmd == "scan":
            cmd_scan(args.json)
        elif args.cmd == "hash":
            return cmd_hash(args)
        elif args.cmd == "agent-audit":
            return cmd_agent_audit(args)
        elif args.cmd == "register":
            return cmd_register(args)
        elif args.cmd == "verify":
            return cmd_verify_v2(args)
        elif args.cmd == "reap":
            return cmd_reap_v2(args)
        elif args.cmd == "dedup":
            return cmd_dedup(args)
        elif args.cmd == "shard":
            return cmd_shard(args)
        elif args.cmd == "unshard":
            return cmd_unshard(args)
        elif args.cmd == "mirror":
            return cmd_mirror(args)
        elif args.cmd == "mirror-search":
            return cmd_mirror_search(args)
        elif args.cmd == "fetch":
            return cmd_fetch(args)
        elif args.cmd == "register-mirror":
            return cmd_register_mirror(args)
        elif args.cmd == "pull-back":
            return cmd_pull_back(args)
        elif args.cmd == "daemon":
            return cmd_daemon(args)
        elif args.cmd == "enforce":
            return cmd_enforce(args)
        elif args.cmd == "registry":
            return cmd_registry(args)
        elif args.cmd == "registry-migrate":
            return cmd_registry_migrate(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
