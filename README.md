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
```

### State

- Registry: `~/workspace/agent-ops/cloud-dog-registry.json`
- Action log: `~/workspace/agent-ops/disk-cleanup-log.md` (append-only)

### Wiring

The `disk-space-guardian` cron (every 10 min) calls:

```bash
python3 /path/to/cloud_dog.py enforce
```

in dry-run evaluation mode, and with `--apply` when usage is over 75%.
See `~/workspace/cron.d/minutely/disk-space-guardian__interval@10m.md`.

### Laws

- NEVER delete anything unverified. Verify-then-delete only.
- NEVER kill in-progress downloads/uploads. In-flight work is sacred.
- Never touch `/tmp/cj-canon`.
- One-store rule: verified 100% on GitHub OR TeraBox = safe to clear.
- Every deletion is logged with its backup location.

### Requirements

Python 3.8+, stdlib only. `gh` CLI required for GitHub verification.

---

## 2. `clouddog/` — full engine package (Python)

Production-grade disk-storage assistant. Local disk is a **cache**, not
storage: CloudDog continuously mirrors files to permanent stores (TeraBox,
GitHub releases, Google Drive), byte-verifies them, journals everything,
and only then makes them eligible for clearing. The owner's standing disk
laws are encoded as code (`clouddog/policy/laws.py`). Built on top of
`cloud_dog.py` — see `Journal.import_registry()` for the bridge between
the two audit trails, and `docs/OPERATIONS.md` for the coexistence notes.

Install and quickstart:

```bash
pip install -e ".[dev]"
clouddog init                                  # write ~/.clouddog/config.yaml
clouddog status                                # disk, journal, stores
clouddog sweep                                 # dry-run plan (default)
clouddog sweep --execute --yes                 # mirror only; never deletes
clouddog store ./big-file.bin --to terabox     # dry-run plan
clouddog store ./big-file.bin --to terabox --execute --yes
clouddog verify --journal-entry 3              # re-verify a journal entry
clouddog watch --once                          # single daemon pass (cron calls this)
```

Docs:

- `docs/DESIGN.md` — architecture, data flows, safety invariants
- `docs/LAWS.md` — the owner's disk laws, quoted verbatim, with how each is encoded
- `docs/OPERATIONS.md` — install, config reference, CLI usage, cron wiring,
  TeraBox credentials, journal, recovery, coexistence with `cloud_dog.py`,
  and what v1 does not do yet
- `docs/PULL-LOG.md` — every open-source project surveyed: what was pulled
  in, what was excluded and why, plus v2 candidates

Safety contract (v1): dry-run is the default; `--execute` needs `--yes`;
`sweep --execute` mirrors only and never deletes (deletion is a future
explicit `reap` step); in-progress work and `.DO_NOT_DELETE`-marked trees
are never touched; protected game files need owner approval.

---

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
