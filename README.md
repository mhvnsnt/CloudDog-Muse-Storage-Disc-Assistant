# Cloud Dog — Muse Storage Disc Assistant

Real disk-hygiene tooling for the Muse VM. Not a dashboard mockup — working
code that enforces the owner's standing law:

> **NO-LOCAL-CLUTTER LAW:** Disk is a cache, not storage. Everything goes to
> TeraBox, Drive, Kaggle, or GitHub — then clears local. All agents, all
> chats, every lane. Disk stays under 75%.

This repo holds three complementary pieces:

1. **`cloud_dog.py`** — the stdlib-only verify-then-delete CLI (live engine,
   wired into the disk-space-guardian cron).
2. **`clouddog/`** — the full Python engine package: multi-store backends
   (TeraBox / GitHub releases / Drive), the disk laws as code, audit
   journal, daemon mode, tests + CI. Extends `cloud_dog.py`, never replaces
   it.
3. **`src/` dashboard + spark scripts** — the Vite/React UI companion and
   lane helper scripts.

---

## 1. `cloud_dog.py` — disk-hygiene engine

### How it works

1. **Scan** — reports disk usage % and the largest consumers in `~/workspace`.
2. **Register** — a lane records a file as verified on a permanent store
   (GitHub release asset or TeraBox), with sha256 + size.
3. **Verify** — re-checks the local file against the store (GitHub release
   asset API: name + size + sha256 digest). TeraBox entries are never
   auto-verified (API path down) — they require a manual live check.
4. **Reap** — verify-then-delete. Only files that pass verification are
   deleted. In-flight files (recently modified, transient suffixes),
   sacred paths (`/tmp/cj-canon`, `agent-ops/checkpoints/`, ssh keys), and
   `.DO_NOT_DELETE`-marked trees are never touched.

Default mode is **dry-run**. Nothing is deleted unless `--apply` is passed.

### Usage

```bash
# disk status
python3 cloud_dog.py scan

# register a file that a lane verified on a GitHub release
python3 cloud_dog.py register --file /path/to/part-aa --store github \
    --repo mhvnsnt/some-repo --release some-tag --asset part-aa

# TeraBox backend (wraps tbupload.py; needs .creds.json activated,
# see agent-ops/TERABOX-SESSION-KEEPER.md)
python3 cloud_dog.py register --file /path/to/video.mp4 --store terabox \
    --remote-dir /CN-Gigaleak/show --asset video.mp4

# Kaggle backend (REST via ~/.kaggle/kaggle.json)
python3 cloud_dog.py register --file /path/to/data.zip --store kaggle \
    --dataset marsfmmg/my-dataset --asset data.zip

# restic backend (deduped, encrypted snapshots; binary at
# ~/workspace/tools/restic/restic; repo password via RESTIC_PASSWORD
# env var — never on the command line, never in the repo)
export RESTIC_PASSWORD="..."
python3 cloud_dog.py register --file /path/to/bigfile.dat --store restic \
    --restic-repo /mnt/archive/restic-repo
# register backs the file up NOW and records the snapshot id; or record a
# pre-existing snapshot: --restic-repo ... --snapshot-id <id>

# verify one file against its store (github | terabox | kaggle | restic)
python3 cloud_dog.py verify --file /path/to/part-aa

# dry-run: show what WOULD be reaped
python3 cloud_dog.py reap

# real: verify each file, delete what passes, log everything
python3 cloud_dog.py reap --apply

# full cycle: scan, then reap if over 75% (or --apply to force)
python3 cloud_dog.py enforce [--apply]

# inspect the registry
python3 cloud_dog.py registry

# torrent -> cloud store with bounded local disk (TorBox cloud torrent,
# Range-stream into rolling parts, upload+verify+delete each part)
python3 cloud_dog.py mirror --magnet 'magnet:?xt=...' --store github \
    --repo mhvnsnt/some-repo --release some-tag --part-mb 100

# continuous enforce loop (pidfile, SIGTERM-clean, journals every cycle)
python3 cloud_dog.py daemon --interval 600
python3 cloud_dog.py daemon --interval 600 --apply   # real deletes
```

### State

- Registry: `~/workspace/agent-ops/cloud-dog-registry.json` (default backend)
  or `cloud-dog-registry.db` when sqlite is selected —
  `--registry sqlite|json` or `$CLOUDEDOG_REGISTRY` (default: json until the
  owner flips it). `registry-migrate` runs the one-shot JSON -> sqlite
  import; the JSON file is kept as a backup and never deleted.
- Action log: `~/workspace/agent-ops/disk-cleanup-log.md` (append-only)
- Audit journal: `~/workspace/agent-ops/cloud-dog-audit.jsonl`
  (structured JSON, one event per line: register, verify, reap,
  mirror_part, mirror_complete, daemon_cycle, ...)

## Store backends (v2)

| Store    | Upload path                  | Verify path                     |
|----------|------------------------------|---------------------------------|
| github   | `gh release upload`          | release asset API (name+size+sha256 digest) |
| terabox  | `tbupload.py` (needs `.creds.json`) | `tbupload.py ls` + size match |
| kaggle   | REST `POST /datasets/create/new` (multipart, `~/.kaggle/kaggle.json`) | REST `GET /datasets/view/{owner/slug}` + size match |

TeraBox auto-verify is allowed ONLY when `.creds.json` exists AND the live
`ls` succeeds. Otherwise the entry stays unverified until a manual live
check — the registry claim alone is never proof.

### Wiring

The `disk-space-guardian` cron (every 10 min) calls:

```bash
python3 /path/to/cloud_dog.py enforce
```

in dry-run evaluation mode, and with `--apply` when usage is over 75%.
See `~/workspace/cron.d/minutely/disk-space-guardian__interval@10m.md`.

Standalone alternative (no cron): `python3 cloud_dog.py daemon --interval 600 [--apply]`

## Laws

- NEVER delete anything unverified. Verify-then-delete only.
- NEVER kill in-progress downloads/uploads. In-flight work is sacred.
- Never touch `/tmp/cj-canon`.
- One-store rule: verified 100% on GitHub OR TeraBox OR Kaggle = safe to clear.
- Every deletion is logged with its backup location (markdown log + JSON audit journal).

### Requirements

Python 3.8+, stdlib only. `gh` CLI required for GitHub verification.
TeraBox needs `~/workspace/tools/terabox-uploader/.creds.json` (one-time
browser-cookie setup). Kaggle needs `~/.kaggle/kaggle.json`. Mirror mode
needs the TorBox connector credential (`custom.torbox` in the Secure Vault).

## Open-source tooling survey (2026-10-17)

Candidates evaluated for the mission; wired only where genuinely useful:

- **rclone** — not installed; no native TeraBox/GitHub-release/Kaggle backends,
  so it does not serve the current stores. Revisit if a store gains an rclone remote.
- **dust / fclones** — not installed; `scan`'s stdlib walker is fast enough
  at this tree size. Revisit if scan becomes a bottleneck.
- **libtorrent** — not installed; mirror mode uses TorBox cloud torrenting
  instead (zero local footprint by design). A direct libtorrent source is a
  future option for non-cached torrents.
- **sqlite3** — present (stdlib + CLI). JSON registry + JSONL journal are
  sufficient today; sqlite is the candidate if concurrent writers appear.

---

## CloudDog v1 engine package + live dashboard (2026-10-17)

Built **on top of** `cloud_dog.py` — same verify-then-delete religion, new
capabilities. Two tools, one mission:

| | `cloud_dog.py` (single file) | `src/clouddog/` package + `clouddog` CLI |
|---|---|---|
| Style | lean stdlib CLI, registry-driven | importable engine library + CLI + daemon + dashboard |
| Backends | github, terabox, kaggle, TorBox-mirror | github, terabox, drive (rclone) |
| Law engine | sacred/in-flight guards | full `policy.py`: SACRED / NEVER_DELETE / STORE / STORE_AND_CLEAR / KEEP_LOCAL per the owner's disk laws |
| Daemon | `daemon` subcommand | `clouddog daemon` (tick loop, --once, --dry-run) |
| Audit | `cloud-dog-audit.jsonl` | `AuditJournal` (JSONL + `disk-cleanup-log.md` format) |
| Dashboard | TS real-disk bridge (`server.ts`) | Python stdlib dashboard, **zero mock data** |
| Torrent | TorBox cloud API mirror | libtorrent streaming ingest |

Both write to the same audit log (`~/workspace/agent-ops/disk-cleanup-log.md`).
The Python dashboard surfaces `cloud_dog.py`'s registry in its lanes view.

### Install

```bash
pip install -e ".[test]"      # or: PYTHONPATH=src python3 -m clouddog.cli ...
```

### CLI

```bash
clouddog scan --path ~/workspace              # policy evaluation per disk law
clouddog store <path> --tier github --remote-ref mhvnsnt/repo@tag
clouddog verify <path> --backend github --locator mhvnsnt/repo@tag/asset
clouddog reap --manifest lane.json --src-dir /path/to/files
clouddog daemon --interval 600 --once --dry-run
clouddog du --path ~/workspace --depth 2       # dust-powered visual du
clouddog dedupe --path ~/workspace             # fclones duplicate report (never deletes)
clouddog dashboard --port 8899                 # live dashboard, 127.0.0.1 only
```

`--dry-run` on everything destructive. Exit codes: 0 ok, 2 usage, 3 failed verify/policy.

### Dashboard

`clouddog dashboard` serves a stdlib-`http.server` API + vanilla-JS frontend
(no build step, no CDN). Every number is live: `df`/`du`, policy scan,
lane manifests (`blob_checkpoint.json`), backend health, audit journal.
Panels: storage overview, clutter table, terminal (`/api/cli`, allowlisted
subcommands), daemon scheduler (generates real systemd/LaunchAgent/Task
Scheduler configs), repo explorer (`gh api`), torrent mirror, compute
(real CPU/RAM/disk; GPU only if `nvidia-smi` exists — no fake vVRAM),
emergency reaper, broadcast drafts (formatting only — never writes
AGENTS.md/MEMORY.md), relocation wizard.

API contract: `dashboard/API.md`.

### Honest capability matrix (2026-10-17, verified live)

| Capability | Status | Notes |
|---|---|---|
| GitHub releases backend | **LIVE** | `gh` CLI; split >2GB; re-download + sha256 verify |
| TeraBox backend | **DEGRADED** | wraps real `tbupload.py`; `.creds.json` absent — needs one-time browser cookie flow (see `agent-ops/TERABOX-SESSION-KEEPER.md`) |
| Drive backend | **WIRED, awaiting config** | rclone v1.75.2 installed at `~/workspace/tools/rclone/`; needs `rclone config` (owner Google OAuth) |
| Kaggle backend | via `cloud_dog.py` | REST, needs `~/.kaggle/kaggle.json` |
| Torrent ingest (libtorrent) | **LIVE** | libtorrent 2.1.1 streaming; pieces upload + delete as they verify |
| Torrent mirror (TorBox) | via `cloud_dog.py mirror` | needs `custom.torbox` Secure Vault credential |
| Policy law engine | **LIVE** | 28 tests; sacred/in-flight/game-file detection |
| Delete-as-you-go reaper | **LIVE** | generalized from the proven scooby `blob_reaper.py` |
| Dashboard | **LIVE** | stdlib server; all endpoints live-data |
| dust / fclones | **LIVE** | `~/workspace/tools/dust`, `~/workspace/tools/fclones`; wired as `clouddog du` / `clouddog dedupe` |
| sqlite | stdlib available | JSON registry + JSONL journal suffice today; candidate if concurrent writers appear |

### Open-source tooling pulled in (2026-10-17)

- **rclone v1.75.2** (`~/workspace/tools/rclone/`) — multi-cloud; wires the Drive backend. Why: Drive is a standing store tier with no uploader.
- **dust v1.2.3** (`~/workspace/tools/dust/`) — visual `du`; `clouddog du`. Why: instant readable disk maps for triage.
- **fclones v0.34.0** (`~/workspace/tools/fclones/`) — duplicate finder; `clouddog dedupe` (report only). Why: duplicates are pure waste; candidates feed the store→verify→clear flow.
- **libtorrent 2.1.1** (pip) — streaming torrent ingest with per-file upload+delete. Why: torrents must never stage multi-GB locally.
- **sqlite3** — stdlib, no install. Not wired yet; documented candidate for when the registry outgrows JSON.

---

## Spark-app dashboard & scripts (2026-10-17)

---

## 2. `clouddog/` — policy engine, Drive store, config, bridge (Python)

Library package that EXTENDS the `cloud_dog.py` v2 engine (which owns the
CLI, the GitHub/TeraBox/Kaggle backends, daemon and mirror modes, and the
audit journal). This package adds the pieces v2 does not have:

- **`clouddog.policy`** — the owner's disk laws as importable, tested
  code: protected game-file matching, in-progress transfer detection, the
  ONE-STORE decision engine (`decide_delete`), and approval gates.
- **`clouddog.stores`** — the importable `Store` interface plus the Drive
  backend (via rclone).
- **`clouddog.config`** — validated YAML configuration.
- **`clouddog.bridge`** — folds `cloud_dog.py`'s registry into the v2
  audit journal (`registry-import` events), so there is one audit trail.

```bash
pip install -e ".[dev]"
python3 -m pytest tests/ -q
```

```python
from clouddog.policy import laws
d = laws.decide_delete("/home/hatch/workspace/game.iso",
                       verified_stores=["github"], owner_approved=False)
# Decision(allowed=False, requires_owner=True, reasons=[...])
```

Docs:

- `docs/DESIGN.md` — architecture, the policy rule order, safety invariants
- `docs/LAWS.md` — the owner's disk laws, quoted verbatim, with how each is encoded
- `docs/OPERATIONS.md` — install, config reference, policy/Drive/bridge
  usage, tests, and what this package does not do
- `docs/PULL-LOG.md` — every open-source project surveyed: what was pulled
  in, what was excluded and why, plus v2 candidates

Safety contract: this package never deletes anything — `decide_delete`
only *decides*; in-progress work is refused with no override; protected
game files require owner approval AND a verified store; secrets are
redacted from every store error.

## 3. Web dashboard (Vite/React scaffold)

This repo also contains the CloudDog web dashboard scaffold: a Vite +
React + TypeScript UI for visualizing storage state (`src/`). Components
include a storage overview, clutter table, daemon scheduler panel, dry-run
modal, relocation wizard, repo explorer, terminal simulator, torrent/cloud
mirror panel, compute offload panel, emergency reaper center, and an agent
memory broadcast view, backed by mock data in `src/data/`.

```bash
npm install
npm run dev
```

---

## 4. Spark-app dashboard & scripts (2026-10-17)

The `src/` dashboard (Vite + React + Tailwind) and the helper scripts below
came from the spark/Gemini build of "Muse Storage Disc Assist". Installed
verbatim per the owner's order. `cloud_dog.py` above is the real engine;
the dashboard is its UI companion.

# Muse Storage Disc Assist 🪄🎻⚡

> **The zero-clutter storage, in-flight stream reaper, 1TB+ cloud torrent mirror, and free compute / virtual VRAM suite.**

[![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Stream Reaper](https://img.shields.io/badge/In--Flight%20Reaper-Active%20Per--Blob-emerald.svg)]()
[![No-Local-Clutter Law](https://img.shields.io/badge/No--Local--Clutter%20Law-Enforced%20%3C75%25-red.svg)]()
[![Free Cloud Vault](https://img.shields.io/badge/TeraBox%20Vault-1%20TB%2B%20Mirrored-cyan.svg)]()

---

### The In-Flight Stream Reaper Rule

> **Everything on disk gets mirrored to GitHub/TeraBox continuously — mid-download, mid-upload, active work, all of it — instead of waiting for 100%. As each file's upload hash verifies against GitHub, the local copy gets deleted on the spot and the lane's progress file stays as the manifest. Disk falls while the upload runs instead of after.**

### 10-Minute Loop Watchdog

The watchdog runs continuously every 10 minutes to:
1. Prune dead download directories and partial chunk shards.
2. Nurse stalled upload lanes through slow proxy flakes.
3. Patch around GitHub file-size blockers.
4. Finalize commit steps so no blob sits unreferenced.

---

### CLI Usage

```bash
# 1. Run in-flight blob reaper for Scooby lane (deletes local blobs on verify)
muse-assist reaper --lane scooby --auto-delete-verified

# 2. Force immediate watchdog sweep
muse-assist watchdog --sweep-now

# 3. Mirror torrent straight to TeraBox 1TB (zero local footprint)
muse-assist mirror-torrent "magnet:?xt=urn:btih:..." --target terabox

# 4. Enforce < 75% constitutional ceiling
muse-assist enforce-law --target-pct 75
```

---

### License
MIT License. Crafted for resilient continuous audio & asset pipeline engineering.
