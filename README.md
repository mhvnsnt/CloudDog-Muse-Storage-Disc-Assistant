# Cloud Dog — Muse Storage Disc Assistant

Real disk-hygiene engine for the Muse VM. Not a dashboard mockup — a working
CLI that enforces the owner's standing law:

> **NO-LOCAL-CLUTTER LAW:** Disk is a cache, not storage. Everything goes to
> TeraBox, Drive, Kaggle, or GitHub — then clears local. All agents, all
> chats, every lane. Disk stays under 75%.

## How it works

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

## Usage

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

# verify one file against its store (github | terabox | kaggle)
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

## State

- Registry: `~/workspace/agent-ops/cloud-dog-registry.json`
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

## Wiring

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

## Requirements

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

## Spark-app dashboard & scripts (2026-10-17)

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
