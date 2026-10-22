# CloudDog Upgrade Research — Vertical + Horizontal Scaling

**Date:** 2026-10-17
**Purpose:** Deep research on tools and architectural ideas to upgrade CloudDog (disk-hygiene engine: scan → verify against cloud stores → delete local → enforce 75% disk ceiling).
**Status:** Research + local testing complete. NOT committed to the CloudDog repo — staged for owner review.

## Supporting docs (in this directory)
- `disk-tools.md` — disk-usage analyzers + duplicate finders (dust, dua, diskus, fclones, czkawka, jdupes)
- `compression-hashing.md` — compression + fast hashing (zstd, lrzip, b3sum, rhash, SQLite registry)
- `cloud-backends.md` — rclone free-tier stores, libtorrent, aria2, Kaggle API, gh CLI, git-annex/git-lfs
- `production-tools.md` — restic, borgbackup, kopia, OpenAssetIO + architectural ideas

---

## TOOLS TESTED ON THIS VM (verified working)

### 1. `dust` v1.2.6 — disk-usage analyzer ⭐ TOP PICK (vertical)
- **License:** Apache-2.0
- **Test:** Downloaded .deb from GitHub releases (bootandy/dust), extracted binary, ran `dust -d 1` on a 1.1GB/2946-file tree — completed in 0.4s with parallel indexing.
- **Why for CloudDog:** `dust -j` emits the whole directory tree as JSON. CloudDog's scan phase can ingest per-directory sizes programmatically instead of shelling out to `du` and parsing text. Flags `-z 100M` (min size) and `-n 50` (top-N) focus on space worth reclaiming.
- **Integration path:** Shell out to `dust -j <path>` from `cloud_dog.py`, parse JSON, feed into the reap decision engine. Fall back to `du` if dust is absent.
- **Install for users:** `sudo apt install du-dust` (Debian 13+/Ubuntu) or prebuilt binary from GitHub releases.

### 2. `b3sum` v1.8.7 — BLAKE3 hashing ⭐ TOP PICK (vertical)
- **License:** CC0 / Apache-2.0 / MIT (public-domain-friendly)
- **Test:** Downloaded `b3sum_linux_x64_bin` from GitHub releases (BLAKE3-team/BLAKE3). Benchmarked on a 100MB random file: **b3sum 0.043s vs sha256sum 0.306s — ~7x faster.**
- **Why for CloudDog:** CloudDog verifies every uploaded part with sha256. Switching the local-side hash to BLAKE3 cuts verification time ~7x on large files. GitHub's release API returns sha256 digests, so CloudDog would still need sha256 for the *remote* comparison — but BLAKE3 can be used for local dedup detection, registry keys, and quick integrity checks where both sides are local.
- **Integration path:** Use BLAKE3 for the local content-addressed registry (`path → blake3`), keep sha256 for GitHub API comparison. Python has a `blake3` PyPI package for in-process hashing (no subprocess needed).
- **Caveat:** GitHub release asset digests are sha256 — CloudDog cannot drop sha256 entirely for the verify-against-GitHub path.

### 3. `rclone` v1.75.2 — universal cloud backend ⭐ TOP PICK (horizontal)
- **License:** MIT
- **Test:** Downloaded `rclone-current-linux-amd64.zip` from downloads.rclone.org, extracted, ran `rclone version` — works.
- **Why for CloudDog:** Already used for Google Drive. The research confirms these additional **free** backends worth wiring:
  - **Backblaze B2:** 10GB free forever, no credit card. `rclone backend: b2`. Best "set and forget" free store.
  - **Cloudflare R2:** 10GB free + 1M Class A / 10M Class B ops/month, **$0 egress**. `rclone backend: s3` (S3-compatible). Best free tier + free egress. (Requires credit card on file even for free tier.)
  - **MEGA:** 20GB free, E2E encrypted. `rclone backend: mega`. Biggest free chunk but transfer quotas can throttle bulk ops — use as extra mirror, not primary.
  - Skip: Storj (no permanent free tier, $5/mo minimum), Dropbox (2GB), OneDrive (5GB), IDrive (free tier uncertain).
- **Integration path:** CloudDog's store abstraction already handles Drive via rclone. Add B2 and R2 as new store backends using the same `rclone copy` / `rclone check` / `rclone lsjson` code path. `rclone lsjson` gives machine-readable name+size+hash for the verify step.
- **Combined free capacity:** B2 (10GB) + R2 (10GB) + MEGA (20GB) + Drive (15GB) = **~55GB free** across four stores, all via one rclone code path.

### 4. `fclones` v0.35.0 — duplicate finder ⭐ TOP PICK (vertical)
- **License:** MIT
- **Test:** Downloaded `fclones-0.35.0-linux-glibc-x86_64.tar.gz` from GitHub releases (pkolaczk/fclones). Ran `fclones group` on a test dir with duplicate files — correctly identified redundant files and reported reclaimable bytes.
- **Why for CloudDog:** Finds duplicate files across the workspace. CloudDog can surface "these N files are byte-identical, K are redundant" and — after verifying one copy is on a store — delete the redundant local copies. `fclones group --format json` gives machine-readable output.
- **Integration path:** New `cloud_dog.py dedup` subcommand: run `fclones group <workspace>`, parse redundant-file list, cross-reference against the registry (is at least one copy verified on a store?), then delete redundant local copies.
- **Note:** The overlay filesystem in this VM doesn't support FIEMAP (harmless warning, slightly slower). Works fine regardless.

### 5. `zstd` — compression (researched, binary not tested)
- **License:** BSD-3-Clause OR GPL-2.0 (dual)
- **Why for CloudDog:** Compress-before-split on 100MB parts cuts transfer time and store quota. `--rsyncable` flag makes re-uploaded parts dedup-friendly. Dictionary compression (`zstd --train`) helps families of similar files. The `libzstd1` system library is already installed; the CLI wasn't available via apt on this VM (would need building from source or a static binary).
- **Integration path:** Optional `--compress` flag on the split/upload path. Skip already-compressed media (MP4/JPG) by extension. Python `zstandard` PyPI package as an alternative to the CLI.
- **Verdict:** Worth integrating, but lower priority than the four above — the win is on transfer/quota, not on the core verify-then-delete loop.

---

## ARCHITECTURAL IDEAS WORTH ADOPTING (from production-tools.md)

1. **Content-defined chunking + hash-keyed addressing (restic/borg/kopia):** Track content by hash, not just by path. Lets CloudDog skip re-uploading identical files and detect local duplicates even when filenames differ. → Pairs with the fclones integration.
2. **Local index cache of path→hash (borg):** Keep a persistent `(path, mtime, size) → hash` cache so repeat scans skip re-hashing unchanged files. Huge speedup on terabyte trees. → CloudDog's scan phase should adopt this.
3. **Policy layering (kopia):** Global → scope-specific policy precedence. CloudDog's LAWS.md could become machine-readable policy objects instead of hardcoded rules. → Feed into the policy-engine work already in progress (PR #8).
4. **Snapshot model with retention (restic):** Every "verified on store" run is a named snapshot with retention (`--keep-daily 7`). CloudDog's registry could adopt snapshot semantics. → Medium-term.
5. **Lock-free registry (kopia):** Optimistic concurrency for parallel lanes writing to the same registry. → Relevant when multiple upload lanes run concurrently.
6. **SQLite for the registry:** The registry is currently JSON. At 10k+ entries, SQLite gives faster queries, atomic writes, and no whole-file rewrites. `sqlite3` is already installed on the VM. → Recommend migrating the registry to SQLite in the next CloudDog version.

**What NOT to adopt:** restic/borg/kopia themselves are *backup* tools — they never delete source files, have no disk-ceiling enforcement, and use proprietary repo formats. CloudDog should borrow their *ideas* (dedup, CDC, policy layering, index caches), not wrap the tools.

---

## HORIZONTAL: MORE STORES & PROTOCOLS

| Addition | Free tier | Effort | Verdict |
|---|---|---|---|
| Backblaze B2 via rclone | 10GB forever | Low (same rclone path) | **Do it** |
| Cloudflare R2 via rclone | 10GB + free egress | Low (same rclone path) | **Do it** (needs CC on file) |
| MEGA via rclone | 20GB | Low (same rclone path) | Do as extra mirror |
| libtorrent Python bindings | N/A (P2P) | Medium | Do for torrent mirror mode (`apt install python3-libtorrent`) |
| aria2c | N/A (downloader) | Low | Useful for multi-connection HTTP downloads; test before wiring |
| Kaggle API datasets | ~free (quota-based) | Medium | Already in use; automate via `kaggle` CLI |
| git-annex / git-lfs | N/A | Medium | Skip for now — GitHub releases already cover the large-file path |

---

## RECOMMENDED WIRING ORDER (matches owner's open question #2 in MEMORY.md)

1. **dust** → scan engine (fastest win, drop-in JSON)
2. **fclones** → dedup pass (reclaims space without any upload)
3. **SQLite** → registry backend (foundation for scale)
4. **b3sum/BLAKE3** → fast local hashing (7x speedup on verify)
5. **B2 + R2 via rclone** → +20GB free stores on the existing code path
6. **zstd** → compress-before-split (transfer/quota win)
7. **libtorrent** → torrent mirror mode (decentralized retrieval)
8. **Policy objects** → machine-readable laws (kopia-style layering)

---

## LICENSES (all free/open-source)
- dust: Apache-2.0 | b3sum: CC0/Apache-2.0/MIT | rclone: MIT | fclones: MIT
- zstd: BSD-3/GPL-2.0 dual | restic: BSD-2 | borgbackup: BSD-3 | kopia: Apache-2.0
- libtorrent: BSD | aria2: GPL-2.0

## NOTES
- All binaries were downloaded to /tmp for testing and **deleted after testing**. Nothing remains on local disk.
- No accounts were created, no cloud stores were configured — the B2/R2/MEGA wiring is documented but not activated.
- The CloudDog repo was NOT touched. This is research-only, staged at `~/workspace/cloud-dog-research/` for owner review.
