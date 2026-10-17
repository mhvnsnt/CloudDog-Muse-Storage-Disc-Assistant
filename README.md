# CloudDog

## CloudDog engine (Python)

Production-grade disk-storage assistant. Local disk is a **cache**, not
storage: CloudDog continuously mirrors files to permanent stores (TeraBox,
GitHub releases, Google Drive), byte-verifies them, journals everything,
and only then makes them eligible for clearing. The owner's standing disk
laws are encoded as code (`clouddog/policy/laws.py`).

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
  TeraBox credentials, journal, recovery, and what v1 does not do yet

Safety contract (v1): dry-run is the default; `--execute` needs `--yes`;
`sweep --execute` mirrors only and never deletes (deletion is a future
explicit `reap` step); in-progress work and `.DO_NOT_DELETE`-marked trees
are never touched; protected game files need owner approval.

## Web dashboard (Vite/React scaffold)

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
