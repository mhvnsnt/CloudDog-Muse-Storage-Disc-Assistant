# HANDOFF — CloudDog-Muse-Storage-Disc-Assistant

**Last updated: 2026-11-22 (America/New_York).**
**Repo:** `mhvnsnt/CloudDog-Muse-Storage-Disc-Assistant` (PUBLIC), default branch `main`.
**Purpose of this file:** any agent that picks up work here reads this FIRST, then the linked docs. It states what we've done, where we're at, and what we're working toward — so the owner can say "go to this repo, pick up the work" and you just know. Update this file in the same PR whenever the state changes (add, don't delete).

## 1. What this repo is

Cloud Dog — the Muse Storage Disc Assistant. Real disk-hygiene tooling for the Muse VM, not a dashboard mockup: working code that enforces the owner's standing law — **disk is a cache, not storage; everything goes to TeraBox, Drive, Kaggle, or GitHub, then clears local; disk stays under 75%.**

Three complementary pieces:
1. **`cloud_dog.py`** — the stdlib-only verify-then-delete CLI (the live engine).
2. **`cloud_mirror_daemon.py`** — the 24/7 mirror daemon.
3. **`stream_blob_reaper.py`** + **`vvram_allocator.py`** — the blob reaper and the VRAM allocator.

Also here: `clouddog_fetch.py`, `cloud_dog_registry.py`, `muse_disc_assist.py`, `muse-assist.sh`, `install.sh`, `dashboard/`, `tools/`, `tests/`, and `docs/research/`. Sibling repo `clouddog-storage` is the store where the files land; this repo is the tooling that moves them.

## 2. Where we're at

### DONE
- **Engine v1 (2026-10-17):** release `engine-v1` — the verify-then-delete CLI live.
- **Fetch proof (2026-10-21):** release `fetch-proof-20261021`.
- **Runtime archive (2026-10-22):** release `clouddog-runtime-archive-2026-10-21`.
- **Research files archived (2026-10-22, PR #51 merged):** research files moved to `docs/research/` (`3a2e283`, `f692258`).
- **Working method statement (2026-10-27):** `WORKING-METHOD.md` landed.

### IN PROGRESS
- Nothing is actively in progress in this repo that the repo itself records. Last push was the 2026-10-27 sweep commit. The daemon itself runs elsewhere — this repo is the code it runs from.
- **PRE-MADE PARTS FIRST rule (open PR #52).** Standing-rule PR, unmerged.

### BLOCKED
- Nothing recorded as blocked in this repo. Note: disk was at ~76% on 2026-11-21 — above the 75% line this tooling enforces — so the daemon's offload work (landing in `clouddog-storage`) is the live pressure, not this repo's code.

## 3. What we're working toward

Disk stays under 75% forever, with nothing ever lost to a cleanup. The bar: every delete is verify-then-delete (100% byte-identical on the store first, pull-back proven, logged) — never delete-then-hope. The daemon mirrors continuously; the reaper only clears what is verified.

## 4. Standing rules that apply here

- **Branch → PR → merge, always.** Never push to main directly. Add-only, zero-conflict PRs, no force pushes.
- **Checkpoint-and-save law:** CHECKPOINT.md first, update after every step; the repo is the save; resume, never restart.
- **No-temp-storage law:** nothing in /tmp or any wipeable location, ever.
- **Safe-storage-first / one-store rule:** nothing leaves local disk until verified 100% on GitHub OR TeraBox (hash + size, pull-back proven), logged to the disk-cleanup log.
- **Never kill in-progress work.** An upload/offload in flight finishes.
- **Never delete game files** except under the one-store rule with teardown logged to the idle queue first.

## 5. Key files and paths

| What | Where |
|---|---|
| Verify-then-delete CLI | `cloud_dog.py` |
| Mirror daemon | `cloud_mirror_daemon.py` |
| Blob reaper / VRAM allocator | `stream_blob_reaper.py`, `vvram_allocator.py` |
| Fetch + registry + assist | `clouddog_fetch.py`, `cloud_dog_registry.py`, `muse_disc_assist.py`, `muse-assist.sh` |
| Config / install | `config.json`, `install.sh` |
| Dashboard / tools / tests | `dashboard/`, `tools/`, `tests/` |
| Research archive | `docs/research/` |
| Releases | `clouddog-runtime-archive-2026-10-21`, `fetch-proof-20261021`, `engine-v1` |
| The store (separate repo) | `mhvnsnt/clouddog-storage` |
| Deletion log (local) | `~/workspace/agent-ops/disk-cleanup-log.md` |
| Open PR | #52 (PRE-MADE PARTS FIRST rule) |

## 6. Open decisions needing the owner

- No decisions are recorded in this repo. Disk-hygiene runs under the standing storage laws; anything that changes the 75% line or the stores is his call.

## 7. How to pick up

1. Read this HANDOFF.md first, then `README.md`, `AGENTS.md`, and `WORKING-METHOD.md`.
2. Check whether the daemon is actually running and what the disk is at before changing any tooling.
3. Check what other agents are already doing before starting anything (never run a second reaper over a live one).
4. Resume from the stated next step — never restart, never re-derive finished work.
5. Land work branch → PR → merge, add-only. Stage finished work to the repo as it lands.
6. Update this HANDOFF.md in the same PR when the state changes. Verify with your own eyes before claiming anything done.
