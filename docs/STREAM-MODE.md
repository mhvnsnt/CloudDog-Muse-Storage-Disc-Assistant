# Stream mode — watch-dir streaming, delete-as-you-go

Owner order (2026-10-19): download lanes write gigabytes to local disk
first and think about storage later — disk sits near full while downloads
sit whole. Stream mode is the fix: a designated **watch dir** is polled;
every file that lands and *settles* is pushed to the store (GitHub
release) in ~100 MB chunks, each chunk is byte-verified against the LIVE
store, and the local bytes are deleted the moment their file is fully
verified (**DELETE-AS-YOU-GO**) — disk falls *while the lane is still
working*, not after the whole job finishes.

Module: `src/clouddog/streamer.py`. Entry points:

```bash
# one pass (dry-run by default; --apply to upload+clear)
clouddog stream --watch-dir /path/to/downloads --repo mhvnsnt/clouddog-storage

# continuous watch for a download lane
clouddog stream --watch-dir /path/to/downloads --apply --loop --interval 10 \
    --chunk-bytes 50M --settle-secs 20

# or let the daemon do it every tick — no restart needed, the file is
# re-read each tick (~/.clouddog/stream-dirs.json):
#   [{"watch_dir": "/path", "repo": "mhvnsnt/clouddog-storage",
#     "tag": "stream-20261020", "chunk_mb": 100, "settle_secs": 300,
#     "apply": true}]
```

## How it works

1. **Settle scan** (`scan_watch`): a file counts as landed only when its
   size+mtime are unchanged for `settle_secs` across scans (state kept in
   the checkpoint manifest, so the check survives restarts). In-flight
   suffixes (`.part`, `.aria2`, `.tmp`, `.downloading`, …), symlinks,
   directories, zero-size files and anything the policy engine calls
   SACRED or KEEP_LOCAL are skipped — an active partial is **never**
   touched (NEVER-KILL-IN-PROGRESS). The policy engine additionally
   treats any file >1 MB modified within the last 5 minutes as sacred,
   so the effective minimum settle is 300 s at the default settings.
2. **Policy gate** (`stream_gate`): game files are flagged *strict* —
   they stream and verify like everything else, but the local copy
   clears only at the strict bar (below).
3. **Chunk + store**: the settled file is hashed, split into chunks
   (default 100 MB — the size the threshold research found survives the
   egress proxy; 485 MB+ parts fail), and each chunk goes through
   `backend.store()`, which uploads it as a release asset named
   `<file>.chunk-NNNN` and **byte-verifies it by re-downloading and
   sha256-comparing against the live store**. Peak extra local disk: one
   chunk of scratch.
4. **Checkpoint after every chunk**: `.clouddog-stream-manifest.json` in
   the watch dir is rewritten atomically (tmp + fsync + replace) after
   each verified chunk. It is **sacred** — never deleted by the streamer
   or the reaper; it is the resume record (a crashed stream resumes at
   the first unverified chunk) and the **sole source** for the next step.
5. **Final manifest from the checkpoint** (Scooby lesson): the published
   `<file>.stream-manifest.json` release asset — whole-file sha256, chunk
   list with per-chunk sha256/locators, and a copy-paste reassemble
   command — is built from the checkpoint record, never by re-walking
   the watch dir (the streamer prunes local bytes as it goes; a re-walk
   would see an empty dir and lose the file at the last step).
6. **Clear**: after every chunk verifies and the manifest is published,
   the source is re-hashed (TOCTOU guard — if it changed since hashing,
   it stays local) and deleted. Every deletion is journaled to the audit
   journal **and** logged with its backup location to
   `~/workspace/agent-ops/disk-cleanup-log.md` (rule 7).

**Game-file strict bar** (owner 2026-10-17, 100% rule): a strict file's
local copy clears only when all three hold — 100% byte-verified store,
retrievability **proven** (each chunk's StoredRef is actually pulled
back from the live store and hash-checked, PR #37 prover), and the
store surfaced to the owner. The CLI takes `--retrievability-proven
--owner-surfaced` as the owner's attestations; the daemon never claims
them, so under the daemon a game file streams, verifies, and *stays
local* until a human runs the CLI with the flags.

**TeraBox is refused for live streams** (H2): its API exposes no remote
checksum, so a chunk cannot be byte-verified before the local bytes
clear. GitHub (and Drive via rclone) tiers stream; TeraBox stays a
manual store target.

## Research — what was evaluated (October 2026)

The brief: streaming/chunked upload-while-downloading + content-
addressed dedup storage that fits **our** stores (GitHub release assets
and TeraBox). Findings, checked against current upstream docs:

| Tool | License | Verdict |
| --- | --- | --- |
| **rclone** | MIT | The best general sync tool (70+ backends, `chunker` overlay, hash checks) and already pulled in here as the Drive backend's engine. But it has **no GitHub-releases backend and no TeraBox backend** (TeraBox exists only in a third-party fork), and nothing in it does settle-scan → chunk → verify → delete-as-you-go against release assets. Also not installed on this VM (friction research 2026-10-19) — installing it is a separate quick win, not this lane. |
| **casync** (systemd) | LGPL-2.1 | The closest algorithmic match: content-defined chunking, chunks addressed by content hash, index files mapping chunk sequences → files. **Patterns adopted** (per-chunk sha256 addressing; a manifest as the file↔chunk index). Not adopted as a tool: it targets OS/filesystem-image distribution with its own chunk-store layout, has no GitHub-release/TeraBox store, and upstream is maintenance-quiet. |
| **git-annex** | GPL-3.0 / AGPL | Powerful content-addressed file tracking inside git, with pluggable special remotes. Too heavyweight and git-repo-centric for a watch dir of raw downloads; no native release-asset streaming or delete-as-you-go. |
| **DVC** | Apache-2.0 | ML experiment/data versioning over git pointers; remotes are S3/GCS/local-style stores. No GitHub-releases or TeraBox remote, no watch/settle semantics. Wrong shape. |
| **restic** | BSD-2-Clause | Excellent deduplicating backup (CDC chunking, S3/SFTP/REST/B2 backends). But data lives in restic's own encrypted repository format — chunks are not plain release assets, there is no GitHub/TeraBox backend, and losing the repo index orphans every chunk. Backup tool, not a streaming store. |
| **borg** | BSD-3-Clause | Same class as restic, SSH-only remotes. No fit for our stores. |
| **IPFS / kubo** | MIT / Apache-2.0 | Content addressing done right (chunked DAG, hash = address), and its block model informed the manifest design. But "on IPFS" is not "on GitHub/TeraBox": pinning our stores would need a bridge service, and retrievability would depend on pinning, exactly what the ONE-STORE rule wants proven *on our stores*. |
| **git-lfs** | MIT | Hard NO (friction research 2026-10-19): GitHub LFS bandwidth quota (10 GB) conflicts with our verify-everything law (every verify re-downloads the bytes). |

**Decision:** no off-the-shelf tool streams into GitHub release assets +
TeraBox with live byte-verify and delete-as-you-go. CloudDog already
owns verified backends for both stores, so stream mode is a thin state
machine on top of them, borrowing casync/IPFS's content-addressing
(per-chunk sha256 + manifest index) and rclone-chunker's stable chunk
naming. Nothing new had to be installed; the pulled-in "tool" is the
pattern set, wired into the existing daemon instead of a competing
binary (owner: extend the existing daemon, no competing tool).

## Live test evidence (2026-10-20)

Setup: a simulated download lane wrote 5 files × 110 MiB (550 MiB total)
sequentially into `/home/hatch/stream-watch-test`, while
`clouddog stream --apply --loop --interval 10 --chunk-bytes 50M
--settle-secs 20` watched it, storing to
`mhvnsnt/clouddog-storage@stream-test-20261020`. A tracer logged the
watch dir every 10 s. (The 300 s policy sacred window applies on top of
the settle window, so files start streaming ~5 min after their last
write — by design.)

- **06:03:29 UTC** — writer finished; watch dir peak **576,717,961 B**
  (5 × 115,343,360 B). Nothing deleted: all files still sacred/settling.
- **06:07:37** — `lane-a.bin` entered streaming; chunks uploaded +
  live-verified one at a time (checkpoint manifest updated after each).
- **~06:27** — `lane-a.bin` **cleared**: all 3 chunks (50 MiB + 50 MiB +
  10 MiB) byte-verified on the store, final manifest asset published
  from the checkpoint, local copy deleted. Watch dir fell to
  **461,375,850 B** (−115,343,360 B) **while `lane-b.bin` was already
  streaming** — disk fell mid-run, not at the end.
- **Retrievability proof**: `lane-a.bin` was reassembled from the
  release assets alone (`cat chunk-0000 chunk-0001 chunk-0002`) and
  sha256-matched the published manifest exactly:
  `205a941ff59ed4915da4283e32d64d11f2d899fc4a6e9e2b73a535f1413c6f78`
  (115,343,360 B).
- The deletion was journaled to `~/.clouddog/audit.jsonl` and logged
  with its backup location to
  `~/workspace/agent-ops/disk-cleanup-log.md` (rule 7).
- The run continued clearing `lane-b.bin`…`lane-e.bin` under the same
  loop; per-chunk timings (~5–8 min per 50 MiB chunk round-trip) were
  dominated by other lanes saturating the shared egress proxy, not by
  the streamer.
