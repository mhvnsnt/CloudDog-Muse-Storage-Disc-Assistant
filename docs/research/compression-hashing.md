# CloudDog Research: Compression & Fast-Hashing Tools

**Date:** 2026-10-17
**Purpose:** Evaluate free/open-source compression and hashing tools for integration into CloudDog (the disk-hygiene engine that splits large files into 100MB parts, verifies with sha256, and keeps a file registry).

> CloudDog constraints to keep in mind: disk is a **cache**, not storage; every registered upload must be verifiable; tools must install via apt on Ubuntu/Debian with no licensing cost.

---

## 1. zstd (Zstandard)

### License
Dual-licensed **BSD-3-Clause OR GPL-2.0-or-later** (free/open-source, commercially safe).
Source: `github.com/facebook/zstd`. Algorithm published as RFC 8878 ("application/zstd", `.zst`).

### What it does
Fast lossless compression algorithm by Yann Collet (Meta). Scales from ultra-fast (level -7) to near-xz ratios (`--ultra -22`), with uniformly fast decompression at every level. Current stable: **1.5.7** (2025-02).

### Why it's relevant to CloudDog
CloudDog splits large files into 100MB parts and uploads them to GitHub/TeraBox. zstd is the single most impactful upgrade to that pipeline:
- **Compress-before-split:** compressing a 100MB part before upload cuts transfer time and store quota usage. Level 3–6 gives zlib-level ratios at 5–10x gzip speed.
- **`--rsyncable`:** periodically syncs compression state so a small edit to a source file doesn't rewrite every downstream compressed byte. This makes re-uploaded/redundant parts **rsync/dedup-friendly** — only changed blocks move. Negligible ratio impact; documented to not work with `--single-thread`.
- **Dictionary compression (`zstd --train ... -o dict`, then `zstd -D dict`):** if CloudDog stores many *similar* files (e.g., per-show media chunks, versioned archives), training one dictionary per family improves ratios ~15–30% on small files without slowing decompression. Also `--patch-from FILE` treats a prior version as a dictionary for diff-style incremental parts.
- **Multi-threaded compression** via `-T0` (all cores); default-on integrity check (`-C`) per frame; `--test` to verify archives.
- **Long-range mode (`--long`)** gives rzip/lrzip-style long-distance dedup with a 128 MiB window when ratios on huge files matter most.

### Key CLI commands/flags
```bash
# Basic: compress (level 3 default, fast), keep original
zstd -k file.bin

# Compress to stdout with threading, level 6
zstd -6 -T0 file.bin -o file.bin.zst

# Rsyncable output (for incremental/dedup-friendly parts)
zstd --rsyncable -3 -T0 file.bin -o file.bin.zst

# Train a dictionary from a family of similar files
zstd --train samples/* -o family.dict
zstd -D family.dict -T0 file.bin -o file.bin.zst

# Max compression (slow) for cold archives
zstd --ultra -22 file.bin

# Verify / list / decompress
zstd -t file.bin.zst          # integrity test
zstd -l file.bin.zst          # archive info
zstd -d file.bin.zst          # decompress

# tar integration (GNU tar ≥ 1.34)
tar --zstd -cf archive.tar.zst /path/to/dir
```

### Performance
- Compression ~20x speed range across levels; decompression speed varies <20% across all levels.
- Typical: level 3–6 rivals gzip ratios at several GB/s on modern CPUs; level 19 rivals xz at ~1/10th the compression time.
- `--rsyncable` costs "negligible" ratio, some speed cost at high thread counts.
- Compression levels run from -7 (fastest) to 22 (max, `--ultra`).

### Drawbacks
- **Decompression is single-threaded** in main `zstd` CLI (parallel `pzstd` exists but isn't widely used) — fine for verify-after-download, irrelevant for CloudDog's write-heavy flow.
- For tiny already-compressed media (MP4, JPG), gains are ~0%; CloudDog should `--exclude-compressed` or skip by extension.
- Not as ubiquitous as gzip for third-party consumers, but `.zst` is RFC-standard now.

### Install (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install zstd
```

---

## 2. lrzip (Long Range ZIP)

### License
**GPLv2** (free/open-source). `github.com/ckolivas/lrzip`, by Con Kolivas.

### What it does
Two-stage compressor for *large* files: an rzip-like first pass removes long-distance redundancy (window scales with RAM), then a second-stage backend compresses the result (lzma default for size, lzo for speed, plus zpaq extreme, bzip2, gzip, or first-stage-only).

### Why it's relevant to CloudDog
Niche fit: lrzip beats zstd/xz on **huge files with long-distance duplication** (e.g., VM images, tarballs of repeated assets, padded game ISOs — CloudDog handles game files). Sliding mmap handles files larger than RAM.

### Key CLI commands/flags
```bash
lrzip file.bin                 # default: lzma backend → file.bin.lrz
lrzip -l file.bin              # lzo backend = fast, bzip2-class ratios
lrzip -z file.bin              # zpaq extreme (slowest, smallest)
lrzip -g -L 9 file.bin         # gzip backend, level 9
lrzip -w 500 file.bin          # limit first-stage window to 50GB (100s of MB)
lrunzip file.bin.lrz           # decompress
lrzcat file.bin.lrz | ...      # decompress to stdout (pipeable)
lrztar /path/to/dir            # tar + lrzip in one step
```

### Performance
- lzo backend: better speed than gzip with bzip2-class compression.
- lzma backend: better than plain lzma/xz on files with long-range redundancy (benchmark: linux.tar → 7.13x ratio with gzip backend).
- Heavily multithreaded backend; first stage is memory-bound.

### Drawbacks (significant for CloudDog)
- **Deprecated by its own ecosystem:** zstd `--long` (128 MiB window, since zstd 1.3.2) covers the same long-range-dedup use case with a maintained tool. lrzip's last release was 0.651 (2022) — maintenance is minimal.
- `.lrz` is a non-standard format; worse tooling on Windows/macOS.
- First stage needs lots of RAM to shine (window = RAM size).
- **Verdict:** skip unless CloudDog benchmarks show zstd `--long` is materially worse on its game-ISO workloads.

### Install (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install lrzip
```
(Unpackaged source fallback: build from `github.com/ckolivas/lrzip`.)

---

## 3. b3sum (BLAKE3)

### License
**CC0-1.0 OR Apache-2.0 OR Apache-2.0 WITH LLVM-exception** (public-domain option — maximally permissive).
Source: `github.com/BLAKE3-team/BLAKE3`.

### What it does
Official CLI for the BLAKE3 cryptographic hash: Merkle-tree-based, natively parallel across threads *and* SIMD lanes, 256-bit output, length-extension resistant (unlike SHA-2). Multithreading is on by default.

### Why it's relevant to CloudDog
CloudDog currently verifies every part with **sha256**. Hashing is CPU-bound once storage exceeds ~1 GB/s (NVMe), and CloudDog hashes every part twice (pre-upload, post-upload) plus re-verifies. BLAKE3 is the highest-impact change to the verify path:
- **~2.8–10x faster than SHA-256** depending on hardware (8.4 GB/s single-thread on Zen 4 vs 3.0 GB/s SHA-256 with SHA-NI; multi-threaded BLAKE3 scales ~11x on 16 cores to ~92 GB/s, while SHA-256 has no parallel mode at all — it flatlines at 3 GB/s).
- Cryptographically secure (128-bit security), so it is a *drop-in security-equivalent replacement* for sha256 in integrity checks.
- Extra modes CloudDog could use later: `--keyed` (HMAC-like per-lane tags), `--derive-key` (lane-specific hash domains), `--length` for longer digests.

### Key CLI commands/flags
```bash
b3sum part.bin > part.bin.b3            # hash a part (uses all cores)
b3sum --check part.bin.b3               # verify (like sha256sum -c)
b3sum --check --quiet part.bin.b3       # quiet verify
b3sum --no-names part.bin | cut -d' ' -f1   # digest only (scripting)
b3sum --num-threads 4 part.bin          # cap threads
b3sum - < pipe                          # hash stdin (pipeable in upload lanes)
b3sum --raw part.bin > part.bin.digest  # raw 32-byte output
```

### Performance
- Single-thread: **~2.8x** SHA-256 with SHA-NI hardware; **~10x** vs software SHA-256.
- Multi-core: scales near-linearly (92 GB/s on 16 cores); SHA-256 does not parallelize — the gap widens with core count.
- Below ~1 GB/s disk throughput, hashing choice doesn't matter (I/O-bound) — but CloudDog targets fast NVMe/SSD where it does.

### Drawbacks
- **Tooling ecosystem:** sha256 is universal (coreutils, `git hash-object`, every web API); BLAKE3 is newer (2020). If a store (GitHub release, TeraBox) wants to re-verify independently, sha256 is more interoperable. Mitigation: store *both* hashes in the registry (b3 for speed, sha256 for interop) — one pass can compute both.
- Rust binary: heavier than a C tool, but installs are trivial.
- Some conservative security teams distrust the reduced 7-round structure — irrelevant for integrity verification.
- `b3sum` is in Ubuntu **universe** (shipped since 22.04, confirmed in noble manpages); may be missing from minimal images — fallback: `cargo install b3sum` or prebuilt release binaries.

### Install (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install b3sum
# fallback: cargo install b3sum   (or prebuilt binary from BLAKE3-team/BLAKE3 releases)
```

---

## 4. rhash

### License
**BSD Zero Clause License (0BSD)** — public-domain-equivalent, maximally permissive. `github.com/rhash/RHash`.

### What it does
"Recursive Hasher": one CLI/library computing 20+ digests (CRC32/CRC32C, MD4/5, SHA1/256/512, SHA3, BLAKE2s/2b, **BLAKE3**, Tiger, TTH, ED2K, GOST, RIPEMD-160, Whirlpool, …), with SFV/BSD checkfile create + verify, recursive directory walks, and magnet/ed2k link generation.

### Why it's relevant to CloudDog
Useful as a **swiss-army verification tool** rather than the primary hasher:
- One pass, many digests: `rhash -a` emits all supported sums — handy when different stores want different algorithms (e.g., sha256 for GitHub convention + BLAKE3 for speed).
- Recursive mode (`rhash --sha256 --recursive dir/ > manifest.sfv`) can generate whole-tree manifests for a lane's output folder — matches CloudDog's "verify everything before reaping" model.
- `-c/--check` verifies manifests; `-u/--update` appends only-new hashes (incremental manifests without re-reading everything).
- librhash (C library) for embedding multi-hash support directly in Python/C helpers.

### Key CLI commands/flags
```bash
rhash --blake3 part.bin                       # single algorithm
rhash --sha256 --bsd part*.bin > parts.sha256 # BSD-format manifest
rhash -a part.bin                             # all supported digests at once
rhash --sha256 --recursive outdir/ > tree.sfv  # whole-tree manifest
rhash -c parts.sha256                         # verify manifest
rhash -u parts.sha256 --recursive outdir/     # add only new files to manifest
rhash --crc32 --simple part.bin               # digest only, no filename
```

### Performance
- Single-threaded C; each algorithm is a solid optimized implementation, but hashing is serial per file (no built-in parallelism like b3sum's multi-threading).
- Its BLAKE3 implementation exists but won't match the Rust `b3sum` SIMD/multithreaded speed.
- Value is **breadth and manifest management**, not raw speed.

### Drawbacks
- Not the fastest at anything — it's the versatile one. For CloudDog's hot path (hash 100MB parts repeatedly), `b3sum` wins by a wide margin.
- Some bundled digests (MD5/SHA1/CRC32) are cryptographically weak — fine for error detection, not tamper evidence.
- Output format differs from coreutils (`sha256sum -c` files are compatible-ish but not identical).

### Install (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install rhash
```

---

## 5. SQLite as registry backend

### License
**Public domain** (free/open-source, no restrictions whatsoever). Python stdlib module `sqlite3` — zero new dependencies.

### What it does (vs JSON)
CloudDog currently keeps its file registry in JSON. SQLite replaces the flat file with an embedded relational database: B-tree tables with indexes, ACID transactions, WAL mode for concurrent readers, `json1` functions for semi-structured fields.

### Why it's relevant to CloudDog
A registry that grows to 10k+ entries (files, parts, hashes, store locations, verify states) hits JSON's structural limits:
- **Writes:** JSON = rewrite the entire file per update → O(n²) for sequential staging. A published benchmark (JSON vs SQLite file-registry): 10,000 adds took **48s JSON vs 23ms SQLite** — ~2,070x.
- **Lookups:** JSON = linear scan O(n); SQLite indexed lookup O(log n) — 10k-entry lookup benchmark: **9ms JSON vs 11µs SQLite** (~840x).
- **Atomicity:** SQLite transactions are atomic even on crash/power loss; JSON writes need manual write-to-temp + rename dances, and a crash between writing the file and updating in-memory state corrupts the registry.
- **Queries CloudDog actually needs:** "which parts of file X are unverified?", "which store has part Y?", "files touched since timestamp" — trivial indexed SQL, painful nested-dict scans in JSON.
- **Single-file portability:** the `.db` is one file like JSON — still trivially uploaded to TeraBox/GitHub as the registry artifact. SQLite 3.9+ `json_extract`/`json_each` even allow keeping some fields schema-flexible.
- SQLite is already in the Python stdlib (`sqlite3`, currently 3.45.1 on this VM) and the `sqlite3` CLI is installed.

### Key patterns CloudDog would use
```python
import sqlite3
db = sqlite3.connect("cloud-dog-registry.db")
db.execute("PRAGMA journal_mode=WAL;")          # concurrent readers, crash-safe
db.execute("""CREATE TABLE IF NOT EXISTS parts(
  part_id TEXT PRIMARY KEY, file TEXT, store TEXT,
  sha256 TEXT, b3 TEXT, size INTEGER, verified INTEGER,
  uploaded_at TEXT)""")
db.execute("CREATE INDEX IF NOT EXISTS idx_file ON parts(file)")
db.execute("CREATE INDEX IF NOT EXISTS idx_verified ON parts(verified)")

with db:  # atomic transaction — commit or rollback as a unit
    db.executemany("INSERT OR REPLACE INTO parts VALUES (?,?,?,?,?,?,?,?)", rows)

# queries JSON can't do cheaply
db.execute("SELECT file FROM parts WHERE verified=0")          # unverified parts
db.execute("SELECT store, COUNT(*) FROM parts GROUP BY store") # per-store census
db.execute("SELECT * FROM parts WHERE file=? AND store=?", (f, s))
```

### Performance
- See benchmarks above: ~2,000x faster bulk adds, ~840x faster lookups at 10k entries; the gap grows with size (quadratic vs log-linear).
- WAL mode reads don't block writers — the guardian and lanes can query the registry while a lane writes.
- Reads of blob data from SQLite are ~35% faster than individual files (SQLite's own "faster than filesystem" benchmark) — irrelevant here since CloudDog stores metadata, not content.

### Drawbacks
- **Migration cost:** one-time conversion from the existing JSON registry; tooling that greps the JSON must be updated.
- WAL creates `-wal`/`-shm` sidecar files — the registry artifact upload must checkpoint first (`PRAGMA wal_checkpoint(TRUNCATE)`) or ship in rollback-journal mode.
- Concurrency model is single-writer — fine for CloudDog (one daemon), but two writers must serialize. Long-running write transactions can block readers briefly.
- Slightly higher code complexity than `json.load`/`json.dump` — use a tiny wrapper module.

### Install (Ubuntu/Debian)
```bash
# Already present: Python stdlib sqlite3 (SQLite 3.45.1 on this VM).
sudo apt update && sudo apt install sqlite3   # CLI tool (optional, for inspection)
```

---

## RECOMMENDATION (ranked for CloudDog integration)

| Rank | Tool | What to do with it | Priority |
|------|------|--------------------|----------|
| **1** | **b3sum (BLAKE3)** | **Replace sha256 as the primary per-part hash** (keep storing sha256 alongside for interop; one pass can compute both via rhash or a small helper). Multi-threaded hashing is the single biggest speedup to CloudDog's verify-heavy loop — sha256 has *no* parallel mode, so the gap grows with every core. | Highest |
| **2** | **SQLite registry** | **Migrate the registry from JSON to SQLite.** At 10k+ entries this is the difference between a 48-second and a 23-millisecond bulk update, plus atomic writes end the corrupt-registry risk class entirely. Zero new deps (stdlib). Do the one-time migration now before the registry grows further. | Highest |
| **3** | **zstd** | **Compress parts before upload** (level 3–6, `-T0`): smaller uploads = faster lanes + less store quota. Use `--rsyncable` on re-uploaded/incremental content so only changed blocks churn. Train per-family dictionaries (`--train`) for many-similar-files lanes (show media, versioned archives). Use `--long` for game-ISO-style huge files. Skip compression for already-compressed media (mp4/jpg). | High |
| 4 | rhash | Keep as a **support tool**: whole-tree manifests (`--recursive`), incremental manifest updates (`-u`), and emitting multi-algorithm manifests when a store demands sha256-format checkfiles. Don't use it for the hot hash path. | Medium |
| 5 | lrzip | **Skip.** zstd `--long` covers its use case with a maintained, RFC-standard tool. Revisit only if a CloudDog benchmark proves lrzip beats zstd-`--long` on its specific game-ISO/tarball workloads. | Low / skip |

### Suggested integration order
1. **SQLite first** — it stabilizes every lane's bookkeeping; migration is a one-time cost that gets more expensive with delay.
2. **b3sum second** — drop-in hash swap in the part-verify path; store both `b3` and `sha256` columns in the new SQLite schema so interop is never lost.
3. **zstd third** — add an optional compress step before split/upload (per-lane flag, skip-list for incompressible media); `--rsyncable` + dictionaries are phase-2 optimizations.
4. rhash as needed for manifest/checkfile generation. lrzip: no action.

### Installation bundle (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install zstd b3sum rhash sqlite3
# lrzip intentionally omitted (see above)
```
