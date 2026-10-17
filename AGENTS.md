# AGENTS.md — CloudDog-Muse-Storage-Disc-Assistant

# ANTI-CLOBBER LAWS (owner 2026-10-17 — the DXMMII 54k-file wipe)

On 2026-10-17, commit cd3bf38 ("DXMMII limb system", PR mhvnsnt/western-heart#221) deleted 54,456 files against 105 additions. The commit message said nothing about deletions. Nobody read the deletion list before merging. The repo was 99.996% gone. These laws exist so that never happens again. Every agent, every repo, every commit, every merge.

1. DELETION CHECK — before every commit, read `git status` and the diff stat. Every deleted file in the diff must be something your task intended to delete. A deleted file your task did not delete on purpose means STOP: do not commit, do not push, report what you found. The working tree is wrong, not the task.

2. OWN THE TREE — never `git add -A` or `git commit -a` in a working tree you did not fully populate yourself. Before committing, verify the tree is complete: file counts match the remote branch you branched from (your task's own additions and changes aside). A tree missing files you didn't delete is damaged — don't sweep it into history.

3. CIRCUIT BREAKER — if a commit would delete more than a handful of files outside your task's scope, STOP and report instead of committing. A big deletion count is a hard stop, not a warning to scroll past. Committing unexplained deletions "and letting someone else figure it out" is the failure.

4. SAY THE DELETIONS — the commit message names every deletion. One deleted file gets one line; many deleted files get a count plus what category went. A commit whose message and diff disagree is a hard failure — don't push it.

5. MERGE GATE — the merger reads the PR's deletion list against the task description before merging. Unexplained deletions block the merge. "CI is green" does not excuse a deletion list the task never asked for. A red, conflicting, or lossy PR stays open with a comment naming the blocker.

(Co-authored with Aider and Copilot per the ALL-AGENTS-WORKFLOW LAW. Cline's seat stays open — its free-tier API was down during drafting.)


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
6. TeraBox entries auto-verify ONLY when `.creds.json` exists AND the live
   `tbupload.py ls` succeeds. Otherwise they need a manual live check
   before any delete. (v2; v1 never auto-verified TeraBox at all.)
7. Every deletion logs to `~/workspace/agent-ops/disk-cleanup-log.md` with
   the backup location, plus a structured event in
   `~/workspace/agent-ops/cloud-dog-audit.jsonl`.
8. Branch → PR → merge (merge commit, never squash). CI must be green.
9. Test before commit: `python3 -m pytest test_cloud_dog.py` must be green,
   and a live `python3 cloud_dog.py scan` + dry-run `reap` must run clean.
10. Mirror mode (`mirror` cmd) never holds more than one part locally
    (default 100MB); each part is uploaded, verified, and deleted before
    the next streams. The manifest (`agent-ops/mirror-manifest-*.json`)
    is the reassembly record — never delete it.
11. Daemon mode (`daemon` cmd) is the standalone alternative to the
    guardian cron. One instance only (pidfile); SIGTERM/SIGINT stops it
    cleanly. Default is dry-run; `--apply` enables real deletes.

## v2 commands (2026-10-17)
- `mirror --magnet URI|--torrent-id ID --store github|terabox|kaggle ...`
  TorBox cloud torrent -> CDN link -> Range-stream into rolling part files
  -> per-part upload + verify + immediate local delete. Needs the
  `custom.torbox` Secure Vault credential. Store dest flags: github needs
  `--repo` + `--release`; terabox needs `--remote-dir`; kaggle needs
  `--dataset owner/slug`.
- `daemon [--interval 600] [--apply] [--pidfile PATH]`
  Continuous enforce loop with pidfile, signal handling, per-cycle audit
  events. Refuses a second instance.
- `register --store` now accepts `github | terabox | kaggle`, with
  `--remote-dir` (terabox) and `--dataset` (kaggle) flags.
- `verify` / `reap` dispatch on the entry's store: github (release asset
  API), terabox (`tbupload.py ls` + size), kaggle (`/datasets/view` + size).

## Registry contract
`~/workspace/agent-ops/cloud-dog-registry.json`:
```json
{
  "version": 1,
  "entries": {
    "/abs/local/path": {
      "store": "github|terabox|kaggle",
      "repo": "mhvnsnt/some-repo",
      "release": "some-tag",
      "asset": "part-aa",
      "remote_dir": "/CN-Gigaleak/show",
      "dataset": "marsfmmg/my-dataset",
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
