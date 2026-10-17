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

## State

- Registry: `~/workspace/agent-ops/cloud-dog-registry.json`
- Action log: `~/workspace/agent-ops/disk-cleanup-log.md` (append-only)

## Wiring

The `disk-space-guardian` cron (every 10 min) calls:

```bash
python3 /path/to/cloud_dog.py enforce
```

in dry-run evaluation mode, and with `--apply` when usage is over 75%.
See `~/workspace/cron.d/minutely/disk-space-guardian__interval@10m.md`.

## Laws

- NEVER delete anything unverified. Verify-then-delete only.
- NEVER kill in-progress downloads/uploads. In-flight work is sacred.
- Never touch `/tmp/cj-canon`.
- One-store rule: verified 100% on GitHub OR TeraBox = safe to clear.
- Every deletion is logged with its backup location.

## Requirements

Python 3.8+, stdlib only. `gh` CLI required for GitHub verification.
