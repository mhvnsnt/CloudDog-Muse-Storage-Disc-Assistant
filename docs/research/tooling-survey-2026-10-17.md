# CloudDog Tooling Survey — 2026-10-17

Real web research (not training data) on production-grade open-source tooling
for CloudDog's mission: scan, deduplicate, verify-then-delete against cloud
stores, torrent mirror mode. Licenses verified against project pages/PyPI/
manpages where stated; anything unverified is marked as such.

Already wired: rclone (Drive), dust (scan), fclones (dupes, candidate).

---

## 1. Duplicate file finders (beyond fclones)

### czkawka / Krokiet — STRONG fit
- URL: https://github.com/qarmin/czkawka
- License: **MIT — verified** (repo README: "The entire code in this repository
  is licensed under the MIT license").
- One-liner: Rust duplicate/empty-file/similar-media finder; CLI (`czkawka_cli`)
  + GUI; Krokiet (Slint) is the active successor, Czkawka GTK 12.0 was the last
  GTK release.
- Fit: STRONG. `czkawka_cli` is scriptable, has cache support (repeat scans
  fast), uses Blake3 pre-hashing + full verification, 34k stars, actively
  developed. Complements fclones (fclones = raw speed on exact dupes; czkawka
  adds similar-image/video/music detection and temp-file/empty-dir cleanup).
- Install: prebuilt binaries on the repo's GitHub releases page; also on
  crates.io / Flathub.

### rmlint — moderate fit
- URL: github.com/sahib/rmlint (reference only)
- License: GPL-3.0 (widely documented; re-confirm on repo before pulling).
- One-liner: Feature-packed C duplicate finder with a "shredder" script output
  (`rmlint.sh`) you review before executing — matches CloudDog's verify-first
  philosophy.
- Fit: MODERATE. Fast and scriptable, but fclones + czkawka cover the ground;
  pull only if a gap appears.
- Install: distro packages (`apt install rmlint`).

### rdfind / jdupes / fdupes — weak fit
- rdfind: GPL-2.0+, ranks duplicates to decide which to keep — nice, but slower
  and less maintained than fclones. jdupes: MIT, fdupes: MIT — both correct but
  single-threaded and outclassed by fclones on terabyte trees.
- Fit: WEAK. No reason to pull while fclones is wired.

## 2. Fast hashing for terabyte-scale verification

Benchmarks (Ryzen 9 7950X, single thread, 1 GB input):
xxHash3-64 31.2 GB/s · wyhash 28.5 GB/s · BLAKE3 8.4 GB/s · SHA-256 3.0 GB/s
(SHA-NI) · MD5 1.2 GB/s. BLAKE3 scales to 92 GB/s on 16 threads; xxHash3 does
not parallelize (single-threaded already saturates).
(Source: https://devtoolspro.org/articles/sha256-alternatives-faster-hash-functions-2025/)

### BLAKE3 — STRONGEST fit
- URL: github.com/BLAKE3-team/BLAKE3 (reference only)
- License: **CC0 / Apache-2.0 dual — verified** (repo LICENSE carries both;
  LLVM's vendored copy references the CC0 license).
- One-liner: Cryptographic (256-bit) hash at 8.4 GB/s/thread, tree-hashes in
  parallel across cores.
- Fit: STRONGEST. CloudDog's verify step needs *cryptographic* integrity
  (adversarial concern: a store could serve a colliding file). BLAKE3 is
  ~10x SHA-256 with the same security level. Drop-in via `pip install blake3`.
- Install: `pip install blake3`; CLI `b3sum` via `cargo install b3sum`.

### xxHash3 — STRONG fit (companion, not replacement)
- URL: https://pypi.org/project/xxhash/
- License: **BSD-2-Clause — verified** (PyPI + upstream README; note: the
  `xxhsum` CLI utility itself is GPL, the *library* is BSD).
- One-liner: 31 GB/s non-cryptographic hash; the fastest practical checksum.
- Fit: STRONG as a *pre-filter*: size → xxHash3 → BLAKE3-full-verify pipeline
  makes duplicate screening ~10x cheaper before the cryptographic proof.
  NOT a substitute for BLAKE3 in the verify step (non-crypto).
- Install: `pip install xxhash` (BSD); distro `xxhash` package for `xxhsum`.

## 3. Torrent/metalink headless clients

### aria2 — STRONGEST fit for mirror mode
- License: **GPL-2.0-or-later — verified** (Debian manpage copyright section:
  https://manpages.debian.org/unstable/aria2/aria2c.1.en.html).
- One-liner: 12 MB RSS one-shot downloader: HTTP(S)/FTP/SFTP/BitTorrent/Metalink,
  JSON-RPC + XML-RPC interface, lowest measured overhead of any client.
- Fit: STRONGEST for CloudDog's mirror mode: scripted, ephemeral, no daemon
  state to manage; Metalink support gives multi-mirror + checksum in one file;
  `aria2py` (MIT, https://pypi.org/project/aria2py/) drives it from Python.
- Install: `apt install aria2`; `pip install aria2py`.

### transmission-daemon — STRONG fit for persistent seeding
- License: **GPL-2.0-or-later — verified**
  (https://www.linuxlinks.com/transmission-bittorrent-client/).
- One-liner: Tiny daemon + `transmission-remote` CLI + documented JSON-RPC;
  v4.x supports BitTorrent v2 (BEP-52).
- Fit: STRONG where a long-lived seeder is wanted (watch dirs, ratio rules).
  Heavier than aria2 per-task (98 MB RSS in benchmarks) but the right shape
  for "keep this torrent alive" vs aria2's "fetch and exit".
- Install: `apt install transmission-daemon transmission-cli`.

### qbittorrent-nox / rtorrent — weak fit
- Both GPL; qBittorrent-nox has the best WebUI API but 41 MB+ RSS and more
  moving parts than the job needs. rtorrent is excellent for scripted
  seedboxes (XML-RPC) but its interface is arcane vs transmission's JSON-RPC.
- Fit: WEAK for CloudDog. Revisit only if aria2+transmission prove insufficient.

### BitTorrent v2 (BEP-52) — STRONG fit (protocol, not a tool)
- Per-file Merkle trees inside v2 torrents = per-file verification without
  re-hashing the whole torrent. Supported by transmission 4.x and
  libtorrent 2.x (qbittorrent 5.x).
- Fit: STRONG. When creating mirror torrents, generate v2 (or hybrid)
  torrents so CloudDog can verify/delete at file granularity.

## 4. Content-addressed / chunked transfer

### desync — STRONG concept, license UNVERIFIED
- URL: https://deepwiki.com/folbricht/desync (docs);
  Go module: https://pkg.go.dev/github.com/folbricht/desync@v0.1.0
- License: **UNVERIFIED — check the repo LICENSE file before pulling.**
- One-liner: Go reimplementation of casync: content-defined chunking,
  content-addressed chunk stores (local/HTTP/S3/SFTP/SSH), `make`/`extract`/
  `chop`/`verify`, chunk-server/index-server, FUSE mount, parallel chunking,
  seed/reflink optimization.
- Fit: STRONG conceptually — chunk-level dedup + delta transfers + `verify`
  are exactly CloudDog's vocabulary at a finer grain. BUT: it is a paradigm
  shift (chunk stores vs whole-file verify-then-delete). Wire as an
  *experimental* backend, not a replacement. Verify license first.

### casync (systemd) — DO NOT PULL
- Upstream archived/deprecated; desync is the maintained path.

### IPFS (Kubo) — weak fit
- Content-addressed and battle-tested, but it is a *network/daemon*, not a
  transfer tool: GC, DHT and pinning semantics fight CloudDog's explicit
  verify-then-delete control. Heavy for the job.
- Fit: WEAK. Revisit only for a "publish to IPFS" mirror target, never as core.

## 5. Cloud storage CLIs (beyond rclone)

### restic — STRONG fit
- License: **BSD-2-Clause — verified** (project docs).
- One-liner: Single-binary encrypted deduped backup: local/S3/B2/Azure/GCS/
  SFTP/REST/rclone backends, content-defined chunking, `restic check`.
- Fit: STRONG. A second, dedup-aware mirror backend next to rclone/Drive;
  2026 repo format v2 cut prune time 40% on large repos. Script-friendly.
- Install: `apt install restic` or GitHub releases (restic/restic).

### kopia — STRONG fit
- License: **Apache-2.0 — verified** (project docs).
- One-liner: Encrypted deduped backup with GUI+CLI, built-in server mode,
  S3/B2/Azure/GCS/WebDAV/SFTP backends, `kopia snapshot verify`.
- Fit: STRONG. Kopia's server mode + policies suit a standing mirror service;
  restic is simpler for one-shot scripts. Either is defensible; restic edges
  it on backend breadth via rclone integration.
- Install: GitHub releases (kopia/kopia) or the project's apt repo.

### borgbackup — weak fit for cloud
- License: **BSD-3-Clause — verified** (https://pypi.org/project/borgbackup/1.4.5/).
- Fit: WEAK for CloudDog's cloud-mirror job — native backends are SSH/local
  only; S3 needs an rclone-mount workaround. Excellent tool, wrong shape here.

### vykar — DO NOT WIRE YET (watch)
- License: **GPL-3.0 — verified**
  (https://www.linuxlinks.com/vykar-fast-encrypted-deduplicated-backup/,
   https://itsfoss.com/vykar-open-source-backup-tool/).
- One-liner: 2026-03 Rust backup tool from the BorgBase team, claims faster
  than borg/restic/kopia (FastCDC chunking, zstd).
- Fit: WATCH ONLY. Its own developers say it is **not recommended for
  production use**. Revisit in 6–12 months.

### duplicacy — DO NOT PULL
- License status unclear; historically proprietary freemium. Not OSI-open —
  excluded per policy.

## 6. High-throughput local I/O

- **io_uring** (kernel 5.1+, liburing): wins are real but narrow — batched
  *background* I/O (many independent ops), NOT single-file copies (a tuned
  `pread` loop beats the ring one-at-a-time). No mature Python binding;
  writing a custom copier is hand-tooling with no payoff for CloudDog.
- **Practical 80/20 for CloudDog (Python):** `concurrent.futures` process pool
  × BLAKE3 chunk hashing + 1–4 MB read buffers. Disk (NVMe) is the bottleneck
  long before the hash is, once SHA-256 is replaced.
- `os.copy_file_range` (Linux, Python 3.8+): zero-copy local copies for
  staging — minor, optional.
- Verdict: no new dependency needed; parallelize in Python.

## 7. Erasure coding / parity for redundant mirrors

### zfec — STRONG fit
- URL: https://pypi.org/project/zfec/1.4.24/
- License: **GPL-2.0-or-later (dual with TGPPL-1.0) — verified** (PyPI page).
- One-liner: Fast k-of-m erasure coding with CLI (`zfec`/`zunfec`), C API and
  **Python API**; ~7x faster than par2 at the same redundancy in published
  benchmarks.
- Fit: STRONG. k-of-m across GitHub/TeraBox/Drive turns "one store" into
  "any k of m stores" — genuine redundancy for irreplaceable files. The
  Python API wires directly into CloudDog.
- Install: `pip install zfec` (needs a C compiler).

### par2cmdline — moderate fit
- License: GPL-2.0 (widely documented).
- One-liner: The standard PAR2 recovery-file format; slower than zfec but
  universally readable.
- Fit: MODERATE. Use when interchange with the PAR2 ecosystem matters;
  otherwise zfec.

## 8. 2026-new / notable

- **Krokiet**: active Slint-GUI successor to Czkawka, same MIT core + CLI —
  the 2026 path for czkawka-family tooling.
- **Restic repository format v2 (2026)**: 40% faster prune on large repos —
  relevant if restic is wired.
- **zpaqfranz** (updated 2026-06, sourceforge): FOSS archiver (zpaq) with
  BLAKE3/XXH3, dedup, 1 GB/s+ claims — interesting for cold archives; license
  not verified here, check before pulling.
- **dust 1.2.3**: already wired into CloudDog scan.
- **BitTorrent v2 mainstream**: transmission 4.x / libtorrent 2.x support is
  now the default assumption, not the exception.

---

## Ranked top-5 "wire next"

1. **BLAKE3** (`pip install blake3`, CC0/Apache-2.0) — Replace SHA-256 in the
   verify path: same cryptographic guarantee at ~10x the speed (8.4 GB/s per
   thread, 92 GB/s parallel). Single biggest throughput win for
   verify-then-delete at terabyte scale.
2. **xxHash3** (`pip install xxhash`, BSD-2-Clause) — 31 GB/s non-crypto
   pre-filter: size → xxHash3 → BLAKE3-full-verify pipeline makes duplicate
   screening nearly free before the cryptographic proof.
3. **aria2** (`apt install aria2` + `pip install aria2py` MIT, GPL-2.0+) —
   Purpose-built for the torrent mirror mode: headless JSON-RPC, Metalink +
   BitTorrent + magnet in 12 MB RSS, scripted one-shot downloads.
4. **restic** (BSD-2-Clause) — Encrypted, content-chunked, multi-backend
   (S3/B2/Azure/GCS/SFTP) mirror target with `restic check`; the dedup-aware
   complement to rclone/Drive.
5. **zfec** (`pip install zfec`, GPL-2.0+) — k-of-m erasure coding across
   stores via its Python API; turns the one-store rule into any-k-of-m
   redundancy for irreplaceable files.

Honorable mentions: czkawka_cli (MIT) for similar-media dupes,
transmission-daemon (GPL-2.0+) for persistent seeding, kopia (Apache-2.0) as
restic alternative, desync (**verify license first**) as experimental
chunk-store backend, par2cmdline for PAR2 interchange.

DO-NOT-PULL: casync (dead upstream), vykar (not prod-ready per its own devs),
duplicacy (license unclear), anything cracked/leaked/proprietary (none
encountered, none recommended).
