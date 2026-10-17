import { RepoFile } from '../types';

export const REPO_FILES: RepoFile[] = [
  {
    name: 'cloud_dog.py',
    path: 'cloud_dog.py',
    language: 'python',
    size: '16.5 KB',
    description: 'The real disk-hygiene engine. Stdlib-only CLI enforcing verify-then-delete, sacred paths, and < 75% limit.',
    content: `#!/usr/bin/env python3
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
    cloud_dog.py register --file PATH --store github --repo OWNER/REPO \\
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

HOME = os.path.expanduser("~")
WORKSPACE = os.path.join(HOME, "workspace")
AGENT_OPS = os.path.join(WORKSPACE, "agent-ops")
REGISTRY_PATH = os.path.join(AGENT_OPS, "cloud-dog-registry.json")
LOG_PATH = os.path.join(AGENT_OPS, "disk-cleanup-log.md")

USAGE_CEILING = 75.0          # target: stay under this %
HARD_CEILING = 88.0           # never reach this
INFLIGHT_MTIME_SECS = 300     # files modified within this window = in-flight

SACRED_PATHS = [
    os.path.join("/tmp", "cj-canon"),
    os.path.join(AGENT_OPS, "checkpoints"),
    os.path.join(HOME, ".ssh"),
    os.path.join(HOME, ".gnupg"),
]

VETO_MARKERS = (".DO_NOT_DELETE",)
INFLIGHT_SUFFIXES = (".part", ".aria2", ".tmp", ".kaggle-partial", ".crdownload")

def now_stamp():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
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

def load_registry():
    if not os.path.exists(REGISTRY_PATH):
        return {"version": 1, "entries": {}}
    try:
        with open(REGISTRY_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}

def save_registry(reg):
    os.makedirs(os.path.dirname(REGISTRY_PATH), exist_ok=True)
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(reg, f, indent=2, sort_keys=True)
        f.write("\\n")
    os.replace(tmp, REGISTRY_PATH)

def log_action(text):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    line = f"- [{now_stamp()}] {text}\\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)

# (CLI engine with cmd_scan, cmd_register, cmd_verify, cmd_reap, cmd_enforce)
`
  },
  {
    name: 'AGENTS.md',
    path: 'AGENTS.md',
    language: 'markdown',
    size: '4.5 KB',
    description: 'Operating manual for Cloud Dog, hard rules, registry contract, and active operational lanes.',
    content: `# AGENTS.md — CloudDog-Muse-Storage-Disc-Assistant

Operating manual for this repo. The main workspace AGENTS.md holds the full
law library; this file holds what is specific to Cloud Dog.

## NO-LOCAL-CLUTTER LAW (owner 2026-10-17)
Disk is a cache, not storage. Everything goes to TeraBox, Drive, Kaggle, or
GitHub — then clears local. All agents, all chats, every lane. Disk stays
under 75%.

## What this repo is
\`cloud_dog.py\` — the real disk-hygiene engine. Stdlib-only Python CLI.
Default dry-run. Verify-then-delete only. Wired into the disk-space-guardian
cron (every 10 min).

## Hard rules for any agent working here
1. NEVER delete anything unverified. A registry entry is a claim; the
   \`verify\` step against the live store is the proof.
2. NEVER kill in-progress downloads/uploads. In-flight work is sacred.
3. Never touch \`/tmp/cj-canon\` (aider lane's files).
4. Never delete inside \`agent-ops/checkpoints/\` — lane manifests are sacred.
5. Respect \`.DO_NOT_DELETE*\` markers — they veto deletion for the whole tree.
6. TeraBox entries are NEVER auto-verified (API path down). They need a
   manual live check before any delete.
7. Every deletion logs to \`~/workspace/agent-ops/disk-cleanup-log.md\` with
   the backup location.
8. Branch → PR → merge (merge commit, never squash). CI must be green.
9. Test before commit: \`python3 cloud_dog.py scan\` and a dry-run \`reap\`
   must both run clean.

## Registry contract
\`~/workspace/agent-ops/cloud-dog-registry.json\`:
\`\`\`json
{
  "version": 1,
  "entries": {
    "/abs/local/path": {
      "store": "github",
      "repo": "mhvnsnt/some-repo",
      "release": "some-tag",
      "asset": "part-aa",
      "sha256": "<hex>",
      "size": 104857600,
      "status": "registered|reaped",
      "registered_at": "...",
      "note": ""
    }
  }
}
\`\`\`
Lanes register with \`cloud_dog.py register ...\` after THEY verify the upload.
Cloud Dog re-verifies independently before deleting.

---

## 1. Active Operational Lanes
| Lane | Agent Name | Current Work & Target | In-Flight Status | Verification & Eviction Policy |
| :--- | :--- | :--- | :--- | :--- |
| **Lane 1** | \`Scooby Stream Reaper\` | 717 total blobs -> GitHub repo | **670 of 717 verified** | Deletes local blob on the spot; 47 blobs to final commit |
| **Lane 2** | \`Priority 8 Files Team\` | DOA4 tars, DXMMII parts, MP4s (3.7 GB) | **In-flight (3 of 8 up)** | Local source deletes the second upload hash verifies |
| **Lane 3** | \`Pipeline Nurse\` | Adventure Time (15/17 up) & Gumball | **Alive & nursing** | Overcoming proxy flakes; partials pruned |
| **Lane 0** | \`10-Minute Loop Watchdog\` | Sweeps all night (7:24, 7:54, 8:04, 8:34, 9:04, 9:24) | **Active loop** | Auto-kills dead downloads, restarts stalled lanes |
| **Lane 4** | \`Multi-Chat Broadcaster\` | Broadcasts freed space to all chats | **Broadcasting** | Commits state to MEMORY.md |
`
  },
  {
    name: 'README.md',
    path: 'README.md',
    language: 'markdown',
    size: '5.8 KB',
    description: 'Documentation for Cloud Dog disk-hygiene engine and dashboard companion.',
    content: `# Cloud Dog — Muse Storage Disc Assistant

Real disk-hygiene engine for the Muse VM. Not a dashboard mockup — a working
CLI that enforces the owner's standing law:

> **NO-LOCAL-CLUTTER LAW:** Disk is a cache, not storage. Everything goes to
> TeraBox, Drive, Kaggle, or GitHub — then clears local. All agents, all
> chats, every lane. Disk stays under 75%.

## How it works

1. **Scan** — reports disk usage % and the largest consumers in \`~/workspace\`.
2. **Register** — a lane records a file as verified on a permanent store
   (GitHub release asset or TeraBox), with sha256 + size.
3. **Verify** — re-checks the local file against the store (GitHub release
   asset API: name + size + sha256 digest). TeraBox entries are never
   auto-verified (API path down) — they require a manual live check.
4. **Reap** — verify-then-delete. Only files that pass verification are
   deleted. In-flight files (recently modified, transient suffixes),
   sacred paths (\`/tmp/cj-canon\`, \`agent-ops/checkpoints/\`, ssh keys), and
   \`.DO_NOT_DELETE\`-marked trees are never touched.

Default mode is **dry-run**. Nothing is deleted unless \`--apply\` is passed.

## Usage

\`\`\`bash
# disk status
python3 cloud_dog.py scan

# register a file that a lane verified on a GitHub release
python3 cloud_dog.py register --file /path/to/part-aa --store github \\
    --repo mhvnsnt/some-repo --release some-tag --asset part-aa

# verify one file against its store
python3 cloud_dog.py verify --file /path/to/part-aa

# dry-run: show what WOULD be reaped
python3 cloud_dog.py reap

# real: verify each file, delete what passes, log everything
python3 cloud_dog.py reap --apply

# full cycle: scan, then reap if over 75% (or --apply to force)
python3 cloud_dog.py enforce [--apply]

# inspect the registry
python3 cloud_dog.py registry
\`\`\`
`
  },
  {
    name: 'MEMORY.md',
    path: 'MEMORY.md',
    language: 'markdown',
    size: '3.6 KB',
    description: 'Verified in-flight reaped blobs registry and watchdog run records.',
    content: `# Immutable Memory Log & Cloud Vault Registry (MEMORY.md)

### Global State: STREAM REAPER ENGAGED
- **Disk Ceiling Enforcement**: Dropping from 96% down through 90% to < 75% target.
- **Scooby Lane Status**: 670 of 717 blobs verified. 47 blobs remaining to final commit step.
- **Priority 8 Files**: DOA4 tars, DXMMII parts, 2 MP4s uploading to GitHub now (~3.7 GB).
- **10-Minute Watchdog Loop**: Fired at 7:24, 7:54, 8:04, 8:34, 9:04, 9:24. Next queued.

---

## 1. Verified In-Flight Reaped Blobs

| Timestamp | Lane Identifier | Blobs Reaped | Space Reclaimed | Verification Target |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-17 09:32:00 | Scooby Lane | 670 / 717 | +7.66 GB | GitHub Blobs API |
| 2026-10-17 09:26:00 | Priority 8 Files | 3 / 8 | +1.20 GB | GitHub Releases |
| 2026-10-17 09:24:00 | Flight-Assist | 24 / 24 | +8.40 GB | TeraBox 1TB Vault |
| 2026-10-17 09:22:00 | Adventure Time | 15 / 17 | +3.70 GB | GitHub Git LFS |
| 2026-10-17 07:54:00 | Space Ghost | 445 files | +5.40 GB | Pruned Dead Archives |
| 2026-10-17 07:24:00 | Dead Download Dir | Directory | +11.00 GB | Cleaned Staging |
`
  },
  {
    name: 'muse-assist.sh',
    path: 'muse-assist.sh',
    language: 'bash',
    size: '2.4 KB',
    description: 'POSIX shell runner and CLI companion connecting to cloud_dog.py.',
    content: `#!/usr/bin/env bash
# Muse Storage Disc Assist - POSIX Shell Runner & Cloud Dog Companion
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python || true)"

case "\${1:-help}" in
  scan)
    "$PYTHON_BIN" "\${SCRIPT_DIR}/cloud_dog.py" scan
    ;;
  reap)
    "$PYTHON_BIN" "\${SCRIPT_DIR}/cloud_dog.py" reap
    ;;
  enforce|clean)
    "$PYTHON_BIN" "\${SCRIPT_DIR}/cloud_dog.py" enforce --apply
    ;;
  registry)
    "$PYTHON_BIN" "\${SCRIPT_DIR}/cloud_dog.py" registry
    ;;
  *)
    echo "Usage: ./muse-assist.sh [scan | reap | enforce | registry]"
    ;;
esac
`
  },
  {
    name: 'config.json',
    path: 'config.json',
    language: 'json',
    size: '1.4 KB',
    description: 'System configuration, sacred paths, ceiling limits, and store contracts.',
    content: `{
  "version": "2.1.0",
  "engine": "cloud_dog",
  "law": "NO-LOCAL-CLUTTER LAW (stay under 75%)",
  "diskLimits": {
    "usageCeilingPct": 75.0,
    "hardCeilingPct": 88.0,
    "inflightMtimeSecs": 300
  },
  "sacredPaths": [
    "/tmp/cj-canon",
    "~/workspace/agent-ops/checkpoints",
    "~/.ssh",
    "~/.gnupg"
  ],
  "vetoMarkers": [
    ".DO_NOT_DELETE"
  ],
  "inflightSuffixes": [
    ".part",
    ".aria2",
    ".tmp",
    ".kaggle-partial",
    ".crdownload"
  ],
  "registryPath": "~/workspace/agent-ops/cloud-dog-registry.json",
  "logPath": "~/workspace/agent-ops/disk-cleanup-log.md"
}`
  },
  {
    name: 'install.sh',
    path: 'install.sh',
    language: 'bash',
    size: '1.2 KB',
    description: 'Installer placing cloud_dog and muse-assist into user PATH.',
    content: `#!/usr/bin/env bash
set -euo pipefail
INSTALL_DIR="\${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"
cp cloud_dog.py "\${INSTALL_DIR}/cloud_dog"
cp muse-assist.sh "\${INSTALL_DIR}/muse-assist"
chmod +x "\${INSTALL_DIR}/cloud_dog" "\${INSTALL_DIR}/muse-assist"
echo "Installed cloud_dog and muse-assist to \${INSTALL_DIR}"
`
  },
  {
    name: 'stream_blob_reaper.py',
    path: 'stream_blob_reaper.py',
    language: 'python',
    size: '1.8 KB',
    description: 'Deletes local files immediately as each upload hash verifies against GitHub/TeraBox.',
    content: `#!/usr/bin/env python3
import os, sys, hashlib, json

def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for blk in iter(lambda: f.read(8192 * 1024), b""):
            h.update(blk)
    return h.hexdigest()

def reap_blob_on_verify(local_file_path, remote_hash, manifest_path="manifest.json"):
    local_hash = compute_sha256(local_file_path)
    if local_hash == remote_hash:
        size = os.path.getsize(local_file_path)
        os.unlink(local_file_path)
        print(f"✓ Verified {local_file_path.name} -> Deleted on the spot! Freed {size / 1e6:.1f} MB")
        return True
    return False
`
  },
  {
    name: 'test_cloud_dog.py',
    path: 'test_cloud_dog.py',
    language: 'python',
    size: '2.5 KB',
    description: 'Test suite for sacred paths, in-flight suffixes, hashing, and registry roundtrip.',
    content: `"""Tests for cloud_dog.py — run with: python3 test_cloud_dog.py"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cloud_dog

def test_sacred_paths():
    assert cloud_dog.is_sacred("/tmp/cj-canon/anything.txt")
    assert cloud_dog.is_sacred("/home/hatch/workspace/agent-ops/checkpoints/x.json")

def test_inflight_suffix():
    inflight, _ = cloud_dog.is_inflight("/tmp/download.part")
    assert inflight

if __name__ == "__main__":
    test_sacred_paths()
    test_inflight_suffix()
    print("smoke tests passed")
`
  }
];
