# AGENTS.md — CloudDog-Muse-Storage-Disc-Assistant

Operating manual for this repo. The main workspace AGENTS.md holds the full
law library; this file holds what is specific to Cloud Dog.

## NO-LOCAL-CLUTTER LAW (owner 2026-10-17)
Disk is a cache, not storage. Everything goes to TeraBox, Drive, Kaggle, or
GitHub — then clears local. All agents, all chats, every lane. Disk stays
under 75%.

## What this repo is
`cloud_dog.py` — the real disk-hygiene engine. Stdlib-only Python CLI.
Default dry-run. Verify-then-delete only. Wired into the disk-space-guardian
cron (every 10 min).

## Hard rules for any agent working here
1. NEVER delete anything unverified. A registry entry is a claim; the
   `verify` step against the live store is the proof.
2. NEVER kill in-progress downloads/uploads. In-flight work is sacred.
3. Never touch `/tmp/cj-canon` (aider lane's files).
4. Never delete inside `agent-ops/checkpoints/` — lane manifests are sacred.
5. Respect `.DO_NOT_DELETE*` markers — they veto deletion for the whole tree.
6. TeraBox entries are NEVER auto-verified (API path down). They need a
   manual live check before any delete.
7. Every deletion logs to `~/workspace/agent-ops/disk-cleanup-log.md` with
   the backup location.
8. Branch → PR → merge (merge commit, never squash). CI must be green.
9. Test before commit: `python3 cloud_dog.py scan` and a dry-run `reap`
   must both run clean.

## Registry contract
`~/workspace/agent-ops/cloud-dog-registry.json`:
```json
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
```
Lanes register with `cloud_dog.py register ...` after THEY verify the upload.
Cloud Dog re-verifies independently before deleting.
