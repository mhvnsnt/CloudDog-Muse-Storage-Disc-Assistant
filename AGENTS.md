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

---

## Spark-app lane registry snapshot (2026-10-17)

Verbatim from the spark/Gemini build's export bundle. Historical snapshot
of the lanes as that app saw them; the live state lives in the registry
and `~/workspace/agent-ops/disk-cleanup-log.md`.

# Multi-Agent Protocol & Broadcast Registry (AGENTS.md)

> **MANDATE: THE NO-LOCAL-CLUTTER LAW & STREAM REAPER RULE**
> *Disk is a cache, not storage. Everything on disk gets mirrored to GitHub/TeraBox continuously — mid-download, mid-upload, active work, all of it — instead of waiting for 100%. As each file or blob hash verifies, the local copy is deleted on the spot.*

---

## 1. Active Operational Lanes

| Lane | Agent Name | Current Work & Target | In-Flight Status | Verification & Eviction Policy |
| :--- | :--- | :--- | :--- | :--- |
| **Lane 1** | `Scooby Stream Reaper` | 717 total blobs -> GitHub repo | **670 of 717 verified** | Deletes local blob on the spot; 47 blobs to final commit |
| **Lane 2** | `Priority 8 Files Team` | DOA4 tars, DXMMII parts, MP4s (3.7 GB) | **In-flight (3 of 8 up)** | Local source deletes the second upload hash verifies |
| **Lane 3** | `Pipeline Nurse` | Adventure Time (15/17 up) & Gumball | **Alive & nursing** | Overcoming proxy flakes; partials pruned |
| **Lane 0** | `10-Minute Loop Watchdog` | Sweeps all night (7:24, 7:54, 8:04, 8:34, 9:04, 9:24) | **Active loop** | Auto-kills dead downloads, restarts stalled lanes |
| **Lane 4** | `Multi-Chat Broadcaster` | Broadcasts freed space to all chats | **Broadcasting** | Commits state to MEMORY.md |

---

## 2. In-Flight Stream Reaper Protocol

```python
# Stream Reaper Invariant:
for blob in lane.pending_blobs:
    upload_hash = github_uploader.push_blob(blob)
    if verify_hash(upload_hash, blob.sha256):
        os.unlink(blob.local_path)          # DELETED ON THE SPOT
        manifest.record(blob.id, upload_hash) # Manifest retains reference
        report_freed_space(blob.size)       # Disk drops while upload runs
```

---

## 3. 10-Minute Watchdog Log Proof
- `07:24`: Cleared 11G dead download dir and stale staging containers.
- `07:54`: Cleared 445 Space Ghost files plus their associated zip archives.
- `08:04`: Purged Gumball failed partial chunks and buffer underrun scratch.
- `08:34`: Patched around GitHub file-size blocker and pruned failed chunk retries.
- `09:04`: Restarted stalled upload lanes after proxy flake; nursed Scooby lane.
- `09:24`: Flight-assist pass finished: 8.4 GB cleared. Every upload lane alive.
- `09:34`: Next sweep queued now.

*Permanently ratified across all chats and sub-agents.*
