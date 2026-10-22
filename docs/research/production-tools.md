# Production Storage & Backup Tools — Research for CloudDog Architecture

Research date: 2026-10-17. Researched by: subagent (web-verified).
Purpose: survey production-grade storage/backup/disk tools to inform CloudDog's architecture (scan → verify against cloud stores → delete local → enforce 75% ceiling).
Rule: research only — nothing was downloaded or installed.

---

## 1. restic

- **License:** BSD-2-Clause (fully free/open-source). Source: https://github.com/restic/restic
- **What it does:** A fast, encrypted, deduplicating backup client written in Go. Each backup run is a snapshot; files are split into content-defined chunks, deduplicated and encrypted client-side, then pushed to one of 20+ backends (S3, Backblaze B2, Azure, GCS, SFTP, REST, local).
- **How the deduplication works (the part CloudDog cares about):**
  - Files are scanned and split into variable-length **blobs** using **content-defined chunking (CDC) with Rabin fingerprints** over a 64-byte sliding window. Small files (<512 KiB) stay whole; larger files become 512 KiB–8 MiB blobs targeting ~1 MiB average.
  - Each blob is keyed by its **SHA-256 storage ID** and stored exactly once per repository — so identical content across files, machines, and snapshots is uploaded/stored only once.
  - Edited large files only upload the *changed* blobs, even if bytes were inserted in the middle (chunk boundaries are content-defined, not position-defined).
- **Architectural ideas CloudDog could borrow:**
  - **Content-defined chunking + hash-keyed content addressing:** instead of tracking whole files by path, track content by hash. This lets CloudDog skip re-uploading identical files and detect local duplicates even when names differ.
  - **Snapshot model:** every "verified on store" run is a named snapshot with retention policies (`forget --keep-daily 7 --keep-weekly 4 …`). CloudDog's registry could adopt the same "verified state at time T" snapshot semantics with retention.
  - **Integrity as a first-class command:** `restic check` verifies the repository; CloudDog should have an equivalent `verify` pass that re-hashes a sample of store blobs.
  - **Packing:** small blobs are packed together (512 KiB–8 MiB packs) to cut API-call overhead — directly relevant to CloudDog's TeraBox/GitHub upload lanes.
- **Key commands/concepts:** `restic init`, `restic backup`, `restic check`, `restic forget` (retention), `restic mount` (FUSE browse), snapshots, blobs, pack files, index, storage IDs.
- **Why NOT to use it directly:** It's a *backup* tool, not a *disk-hygiene* engine — it never deletes source files, has no disk-ceiling enforcement, and its repositories are proprietary-format blobs, not the human-browseable file-per-file layout CloudDog already maintains on GitHub/TeraBox. It also has no TeraBox backend (closest is rclone bridging).
- **Install (Debian/Ubuntu):** `sudo apt install restic` (also available as a static single binary from GitHub releases).

---

## 2. borgbackup ("Borg")

- **License:** BSD-3-Clause. Source: https://borgbackup.org
- **What it does:** A deduplicating archiver with authenticated client-side encryption and pluggable compression (lz4 / zstd / zlib / lzma). Very fast for local/SSH repositories; archives are mountable via FUSE for browsing.
- **How its deduplication works:** Content-defined chunking like restic (variable-length chunks, chunk boundaries from content), with a **local files/chunks index cache** for quick detection of unmodified files — so repeated scans are fast without re-reading everything. Dedup is repo-wide: identical chunks stored exactly once, independent of filenames, paths, or timestamps (renames/moves don't break dedup).
- **Architectural ideas CloudDog could borrow:**
  - **Local index cache of file→chunk hashes:** CloudDog's scan phase could keep a persistent cache (`path, mtime, size → hash`) so repeat scans skip hashing unchanged files — huge speedup on terabyte trees.
  - **Compression before upload:** game/media assets often compress well at the repo level; a `--compression zstd` style toggle for WIP/staging areas could stretch cloud storage.
  - **Append-only mode:** a repository mode where clients can add but not delete — CloudDog could adopt this for its audit journal / registry to make "verify-then-delete" tamper-evident.
  - **Mountable archives (FUSE):** `borg mount` lets you browse any archive as a filesystem — CloudDog could mount its registry/audit as a virtual tree for owner inspection.
- **Key commands/concepts:** `borg init --encryption=repokey`, `borg create`, `borg check`, `borg prune` (time/count retention), `borg mount`, `borg serve` (remote side), archives, chunks, segments, files cache.
- **Why NOT to use it directly:** Natively supports only local and SSH repositories — no cloud-object backend at all (S3/B2/TeraBox need rclone workarounds), single-threaded backups by default, and like restic it never deletes source data or enforces disk ceilings. CloudDog's TeraBox/GitHub backend model is fundamentally different.
- **Install (Debian/Ubuntu):** `sudo apt install borgbackup` (single-file binaries also available upstream). Companion automation tool **borgmatic** (GPL-3.0, YAML config + scheduling + retention + pre/post hooks) is the "recommended way to run borg in production."

---

## 3. Kopia

- **License:** Apache-2.0. Source: https://github.com/kopia/kopia
- **What it does:** A modern Go backup tool (CLI + built-in web UI) with content-defined-chunking dedup, AES-256-GCM or ChaCha20 client-side encryption, per-policy compression (zstd, gzip, s2, lz4), lock-free concurrent operations, and native cloud backends (S3, B2, Azure, GCS, WebDAV, SFTP, local). Includes a built-in repository server mode.
- **Key features relevant to CloudDog:**
  - **Layered policies (global → host → user → path):** retention, compression, scheduling, and exclusion rules that inherit and override down a hierarchy. This is the closest existing analog to CloudDog's "laws as code" — policy-driven, not hardcoded.
  - **Lock-free / optimistic-concurrency repository:** multiple writers can snapshot simultaneously without lock contention — relevant if CloudDog ever runs parallel lanes against the same registry.
  - **Content-addressable, globally deduplicated storage:** identical chunks deduped repo-wide across machines, same as restic/borg.
  - **Built-in consistency checks** (`kopia snapshot verify`) and redundant metadata storage — model for CloudDog's verify pass.
  - **Mountable snapshots** (`kopia mount`) and a web UI for browsing/policy editing.
- **Architectural ideas CloudDog could borrow:**
  - The **policy layering model** — CloudDog's LAWS.md could become machine-readable policy objects with global/default/scope-specific precedence, exactly like Kopia policies.
  - **Lock-free registry design** — if two lanes verify+register concurrently, optimistic concurrency avoids the lock-file serialization that restic/borg suffer from.
  - **Integrity verification as a scheduled policy** rather than a manual command.
- **Key commands/concepts:** `kopia repository create/connect`, `kopia policy set`, `kopia snapshot create`, `kopia snapshot verify`, `kopia server start`, snapshots, manifests, content manager, policies.
- **Why NOT to use it directly:** Younger/medium maturity vs borg/restic, proprietary repository format, no TeraBox backend, and again it's backup software — no source-deletion, no disk-ceiling enforcement, no per-file human-readable store layout.
- **Install (Debian/Ubuntu):** not in default apt repos; official APT repository per kopia.io docs, or `.deb` from GitHub releases (`kopia/kopia`).

---

## 4. OpenAssetIO

- **License:** Apache-2.0 (Academy Software Foundation project, originally developed by Foundry).
- **What it does:** An open **interoperability standard/API** between creative tools ("hosts": DCCs, scripts) and asset management systems ("managers": DAM/MAMs). Tools reference assets by **identity (Entity Reference)** instead of filesystem paths; the manager resolves identity → location/versions on demand, and tools can publish new versions back through the same API.
- **Key concepts:** Entity Reference (identity, not path), ManagerInterface (host↔manager contract), Traits (typed property sets describing an entity), resolve / register / publish operations. It is an API layer — it deliberately does *not* define storage layout, data structures, or how the manager organizes data.
- **Is it relevant to CloudDog?** Only loosely and at the *design* level:
  - **Identity-over-path thinking:** CloudDog's registry currently tracks files by path. OpenAssetIO's lesson is to give each file an identity independent of where it lives, with location as a *resolvable property* — useful as CloudDog moves files between disk, GitHub, and TeraBox.
  - **Traits/metadata model:** OpenAssetIO's typed trait sets are a good pattern for CloudDog's registry entries (path, hash, size, store location, verification status, timestamps as one typed record).
  - **Not a fit for adoption:** It's a VFX-pipeline integration API (C++/Python SDK for DCC tools), requires building/adopting a "Manager" implementation, and solves artist-tool interop — not disk hygiene. Heavyweight for CloudDog's needs.
- **Why NOT to use it directly:** Solves a different problem (cross-tool asset interop in VFX pipelines, not storage management), still a young/sandbox-stage standard, and introduces SDK + manager-implementation overhead with zero disk-cleaning functionality.
- **Install:** Python package `openassetio` via PyPI (`pip install openassetio`); C++ SDK builds from the ASWF GitHub repo. Not in Debian/Ubuntu apt.

---

## 5. Other production-grade tools

### 5a. ncdu — disk usage analysis
- **License:** MIT.
- **What it does:** ncurses-based interactive disk usage analyzer; scan a tree, navigate by size, and delete from the TUI. Modern versions (2.9+) support parallel scanning (`-t8`), binary export (`-O`) for huge trees, and extended file info (`-e`).
- **CloudDog relevance:** the gold standard for "what's eating the disk" triage; CloudDog's scan phase should produce ncdu-style ranked output (and consider `-O` export import for cross-run diffing).
- **Key commands:** `ncdu /path`, `ncdu -o- / | …` (scriptable), `ncdu -t8` (parallel).
- **Why not directly:** interactive/manual only — no verification, no policy engine, no cloud awareness.
- **Install:** `sudo apt install ncdu`.

### 5b. agedu — *wasted* space analysis
- **License:** MIT (Simon Tatham).
- **What it does:** Scans a tree and reports disk usage **correlated with last-access time** — distinguishing "large and in use" from "large and untouched for months" (the actual waste). Query modes: text (`-t`), age-filtered (`-a 6m`), or a self-contained web-server report (`-w`).
- **CloudDog relevance:** This is the single most CloudDog-aligned idea in the list: **staleness (atime) as a first-class deletion signal.** CloudDog's decide_delete engine should weight last-access time alongside verified-store status — an old, verified, untouched multi-GB file is the top reap candidate.
- **Key commands:** `agedu -s /home/fred`, `agedu -t /home/fred -a 6m`, `agedu -s /home/fred -w -R`.
- **Why not directly:** read-only analysis tool; also note many filesystems mount with `relatime`/`noatime`, which degrades atime accuracy (CloudDog should fall back to mtime/ctime when atime is unreliable).
- **Install:** `sudo apt install agedu`.

### 5c. tmpreaper — age-based temp cleanup
- **License:** GPL (Debian package; historically GPL-2).
- **What it does:** Recursively removes files/empty dirs in given directories not accessed within a time threshold (default: atime; `--mtime` option). Security-hardened against symlink race attacks (no chdir(".."), no symlink following, no cross-filesystem). Dry-run via `--test`.
- **CloudDog relevance:** The model for a *safe, scoped, scheduled reaper*: explicit allow-listed directories, test mode, race-condition hardening, runtime cap. CloudDog's reap should support a `--test`/dry-run mode and the same "empty dirs + regular files only" conservatism.
- **Key commands:** `tmpreaper --test 7d /tmp`, `tmpreaper 30d /var/tmp` (config-driven via `/etc/tmpreaper.conf` + cron).
- **Why not directly:** Deletes by age alone with no store-verification concept — the exact opposite of CloudDog's verify-first law.
- **Install:** `sudo apt install tmpreaper` (runs from cron; Debian ships it disabled-by-default via config).

### 5d. BleachBit — system cleaner
- **License:** GPL-3.0.
- **What it does:** CCleaner-style cleaner: deletes caches, logs, temp files, browser data per selectable "cleaners"; has secure file shredding and a CLI (`bleachbit -c`).
- **CloudDog relevance:** A catalog of **known-safe cleanup rules** (caches, logs, thumbnails) — CloudDog could ship a "safe-cleaners" policy set for always-regenerable data (e.g., `~/.cache`, `__pycache__`, thumbnail dirs) that doesn't need store verification because it's regenerable. Czkawka (MIT) is the modern duplicate-finder complement.
- **Why not directly:** GUI/desktop-oriented, pattern-based rather than content-verified, misuse can nuke important data.
- **Install:** `sudo apt install bleachbit` (BleachBit 5.0 .deb also available upstream).

### 5e. rclone — cloud backend swiss-army knife
- **License:** MIT.
- **What it does:** Sync/copy/mount 70+ cloud storage providers with checksum verification (`--checksum`), server-side copies, and an `rclone mount` FUSE layer.
- **CloudDog relevance:** Already partially in the picture (Drive backend). Its `--checksum` sync and `check` command are the reference implementation for "verify local vs store" — CloudDog's TeraBox verify pass should mirror `rclone check`'s semantics (size+hash compare, report-only by default).
- **Install:** `sudo apt install rclone` (upstream also ships a self-updating binary; `rclone selfupdate`).

### 5f. VFX-studio storage patterns (conceptual, not a tool)
Studios don't run one magic tool — they run a **tiered lifecycle**: online (fast NVMe/SSD for active shots) → nearline (spinning disk/NAS for recent projects) → offline/cloud/tape (LTO via LTFS, S3 Glacier tiers) with **checksum manifests at every transition** ("a tape backup that has not been verified is not a backup — it is a hope" — AnimationTech). Media Asset Managers (CatDV, Xendata, iconik) act as the registry-of-truth mapping asset identity → which tier holds it, mirroring OpenAssetIO's identity-over-path idea. Key borrowable concepts:
- **Explicit storage tiers with promotion/demotion policies** (hot/warm/cold) — maps directly onto CloudDog's disk vs GitHub vs TeraBox model.
- **Checksum manifests written at ingest, verified on read-back** (md5deep/hashdeep, xxHash for speed) — CloudDog should generate a hash manifest at upload time and re-verify from it, not just trust the upload HTTP 200.
- **Immutable/archive tiers** (Glacier, tape WORM) for "verified, never touched again" data — the end state CloudDog is building toward.

---

## Comparison table

| Tool | License | Dedup | Encryption | Cloud backends | CloudDog-relevant idea |
|---|---|---|---|---|---|
| restic | BSD-2 | CDC (Rabin) | AES-256-CTR+Poly1305 | 20+ (S3/B2/Azure/GCS/REST) | CDC chunking + hash-keyed content addressing; snapshot model; `check` |
| borgbackup | BSD-3 | CDC | AES-256-CTR+HMAC-SHA256 | local/SSH only | local files/chunks index cache (fast rescan); compression; append-only mode |
| Kopia | Apache-2.0 | CDC | AES-256-GCM/ChaCha20 | S3/B2/Azure/GCS/WebDAV | layered policies (laws-as-code model); lock-free registry |
| OpenAssetIO | Apache-2.0 | n/a | n/a | n/a | identity-over-path; typed metadata traits (design inspiration only) |
| ncdu | MIT | n/a | n/a | n/a | ranked usage triage; parallel scan |
| agedu | MIT | n/a | n/a | n/a | **atime staleness as deletion signal** |
| tmpreaper | GPL | n/a | n/a | n/a | safe scoped reaper: allow-list, --test, race hardening |
| BleachBit | GPL-3.0 | n/a | n/a | n/a | catalog of regenerable-data cleaners |
| rclone | MIT | n/a | n/a | 70+ | `--checksum` verify semantics (`rclone check`) |

---

## RECOMMENDATION — architectural ideas CloudDog should adopt

Adopt the **ideas**, not the tools — none of restic/borg/kopia fits CloudDog's actual job (verify-then-delete with a disk ceiling across GitHub/TeraBox), and all three use proprietary repo formats incompatible with CloudDog's human-browseable stores. In priority order:

1. **Content-addressed registry (from restic/kopia).** Registry entries keyed by content hash (SHA-256, xxHash for speed on large media), not just path. Gives CloudDog free cross-file dedup detection ("this 4GB ISO already exists on TeraBox under a different name — skip upload"), cheap change detection, and a tamper-evident audit trail. A `(path, mtime, size) → hash` **persistent files/chunks index cache à la borg** makes repeat scans O(changed) instead of O(everything).
2. **Snapshot semantics for verification (from restic/kopia).** Every enforce pass records a named snapshot: what was scanned, what verified hash-identical on which store, what was reaped. Retention policy on snapshots (`keep-last N`) keeps the audit journal bounded — this is CloudDog's existing audit.jsonl grown up.
3. **Layered policy engine (from Kopia).** CloudDog's LAWS.md is currently prose; formalize it as global → scope → path policies (default rules, sacred-path overrides, per-lane exceptions) with explicit precedence. This is the `decide_delete` 5-rule engine's natural next step.
4. **Staleness as a deletion signal (from agedu).** Add last-access time to the reap scoring: `score = f(size, atime age, verified-on-store, in-flight?, sacred?)`. Old + verified + untouched = reap first. Caveat: check the filesystem's atime behavior (relatime/noatime); fall back to mtime/ctime.
5. **Verify like rclone check / restic check.** The verify pass should compare size + hash against the store manifest and report mismatches — never trust upload success alone. Studio practice: checksum manifest written at ingest, verified on read-back.
6. **Safe-reaper discipline (from tmpreaper).** Every destructive pass gets `--test`/dry-run parity, allow-listed scopes, no symlink following, and empty-dirs-only removal — most of which CloudDog already does; codify the rest.
7. **Regenerable-data fast lane (from BleachBit/Czkawka).** A policy class for data that needs no store verification because it's regenerable (`__pycache__`, `~/.cache`, thumbnails, build artifacts) — reaps without upload, still logged.

**Explicitly do NOT adopt:** OpenAssetIO as a dependency (wrong problem domain — VFX tool interop, not disk hygiene; take only the identity-over-path design lesson). Do not reimplement borg/restic repos — CloudDog's value is precisely its plain-file, browsable, owner-verifiable stores.

---

*Sources consulted: restic.readthedocs.io (design doc), borgbackup.org + borgbackup.readthedocs.io, kopia.io + github.com/kopia/kopia, docs.openassetio.org, aswf.io, Debian manpages (agedu, tmpreaper), ubuntu.com/canonical.com disk-hygiene guides, animationtech.tv archiving guide, frame.io checksum workflow guide, selfhosting.sh comparison tables.*
