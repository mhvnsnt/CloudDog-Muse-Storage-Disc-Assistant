# CloudDog Backend & Tooling Research
*Research date: 2026-10-17. Research-only; nothing downloaded, no accounts created. Free tiers verified against 2026 pricing sources — re-verify before wiring anything to billing.*

---

## 1. rclone backends + free cloud stores

### rclone (the meta-tool)
- **License:** MIT (free/open-source).
- **What it does:** One CLI/binary that treats 70+ cloud stores as interchangeable filesystems (`copy`, `sync`, `check`, `mount`, `serve`, RC API).
- **Why it's relevant:** CloudDog already uses rclone for Google Drive. Every backend below plugs into the *same* CloudDog verify-then-reap code path — one command family for all stores.
- **Install (Ubuntu/Debian):**
  ```bash
  sudo apt install rclone            # distro version (older)
  curl -fsSL https://rclone.org/install.sh | sudo bash   # latest stable to /usr/bin/rclone
  rclone version && rclone help backends
  ```
- **Key commands CloudDog would use:**
  ```bash
  rclone config create <remote> b2          # non-interactive remote setup (or s3, mega, etc.)
  rclone copy ./part.bin <remote>:bucket/clouddog/ --checksum -P
  rclone check ./local <remote>:bucket/clouddog/ --one-way   # verify-then-delete
  rclone lsjson <remote>:bucket/clouddog/   # machine-readable listing (name+size+hash)
  ```

### Free-tier candidates (rclone-supported)

| Store | Free tier (2026) | Storage cost above free | Egress | rclone backend | Verdict |
|---|---|---|---|---|---|
| **Cloudflare R2** | 10 GB storage + 1M Class A + 10M Class B ops/month | $0.015/GB-mo | **$0 (all egress)** | `s3` (S3-compatible) | Best free tier + free egress |
| **Backblaze B2** | **10 GB free forever**, no credit card | $6/TB-mo | Free up to 3× stored/mo, then $0.01/GB; free unlimited via Cloudflare | `b2` or `s3` | Best "set and forget" free store |
| **MEGA** | **20 GB free**, E2E encrypted by default | ~$4.99/mo plans | Transfer quotas on free (throttled/bandwidth-capped) | `mega` | Biggest free chunk; flakier API |
| Google Drive | 15 GB (shared with Gmail/Photos; note: one 2026 source says new accounts start at 5 GB, 15 GB needs verified phone) | Google One from $1.99/mo | Free | `drive` (already wired) | Already in CloudDog |
| pCloud | 10 GB free | one-time lifetime plans ($199/500GB) | Free | `pcloud` | Backup option, smallish |
| Icedrive | 10 GB free | — | — | ❌ no native backend (would need WebDAV/FTP) | Skip |
| IDrive | Free plan is **uncertain** — multiple 2026 reviews still show "Basic 10 GB free", but one review says the free plan was discontinued | Mini 100 GB $2.95/yr | Free | via **IDrive e2** S3-compatible endpoint | Deprioritize until verified |
| OneDrive | 5 GB free | Microsoft 365 / from $1.99/mo | Free | `onedrive` | Tiny tier; not worth the OAuth overhead |
| Dropbox | 2 GB free (+ 3-device limit) | from $9.99/mo | Free | `dropbox` | Too small; skip |
| **Storj** | ❌ **NO permanent free tier** — 25 GB 30-day trial only | $7/TB-mo + **$5/mo minimum fee** | 1× stored volume free on new tiers, else $0.005–0.02/GB | `storj` or `s3` | **Do not use** — min-fee billing hazard |

**B2 hard limits CloudDog needs to know:** max file 10 TB via multipart (5 GB single-PUT max, 5 MB min part, ≤10,000 parts); no minimum retention; no minimum charge. 100 MB parts are trivially fine.

**R2 notes:** requires a credit card on the account even for the free tier; free tier is Standard class only. S3-compatible endpoint → CloudDog's S3 code path works unchanged.

**MEGA caveat:** rclone's `mega` backend works but MEGA's free accounts have transfer quotas that can stall bulk verification; fine as an extra mirror, not as the primary store.

**Sources:** rclone backend list (Wikipedia/rclone docs); free-tier figures from devopsboys.com (2026), cloudpicked.com (Sep 2026), datastorage.com (Sep 2026), blog.mega.io, cloudzat.com (Sep 2026).

---

## 2. libtorrent (Python bindings) — torrent mirror mode

- **License:** BSD (libtorrent rasterbar; the Python bindings ship with it). Free/open-source.
- **What it does:** Full-featured BitTorrent engine (DHT, magnet links, encryption, seeding) with first-class Python bindings. Powers qBittorrent/Deluge.
- **Why it's relevant:** CloudDog's "torrent mirror mode" — publish a torrent for an archived blob set and let peers (other CloudDog nodes, the owner's machines) pull it, giving a decentralized retrieval path independent of any cloud account.
- **Install (Ubuntu/Debian):**
  ```bash
  sudo apt install python3-libtorrent          # distro package, simplest
  # or for a venv (needs system dev libs):
  sudo apt install libtorrent-rasterbar-dev
  pip install python-libtorrent               # PyPI wheel available
  ```
- **Key API calls CloudDog would use:**
  ```python
  import libtorrent as lt
  ses = lt.session({'listen_interfaces': '0.0.0.0:6881'})
  # download from magnet:
  h = lt.add_magnet_uri(ses, magnet_uri, {'save_path': './out'})
  # poll:
  while not h.is_seed():
      s = h.status()
      print(s.progress, s.download_rate)
      ses.wait_for_alert(1000)
  # create a torrent for CloudDog blobs:
  fs = lt.file_storage(); lt.add_files(fs, './blobs/')
  t = lt.create_torrent(fs); lt.set_piece_hashes(t, '.')
  open('archive.torrent','wb').write(lt.bencode(t.generate()))
  ```
- **Stability:** Very stable and mature (v2.0+ line; 20+ years of production use). Python bindings are actively maintained.
- **Drawbacks / reasons not to use:** Heavier dependency than a downloader; seeding from this VM means holding ports open and burning bandwidth; DHT/magnet bootstrap can be slow for unpopular torrents. If CloudDog only ever *downloads* (never seeds/publishes), `aria2` is simpler.

---

## 3. aria2 / aria2c — multi-protocol downloader

- **License:** GPL-2.0-or-later (free/open-source).
- **What it does:** Single static-ish CLI that downloads over HTTP(S), FTP, SFTP, BitTorrent, and Metalink — with segmented/multi-connection downloads, resume, and an RPC daemon mode.
- **Why it's relevant:** CloudDog's *download* pipeline (pull-backs from stores, fetching upstream assets, grabbing release tarballs). One tool for every protocol; dead simple to shell out to from Python.
- **Install (Ubuntu/Debian):**
  ```bash
  sudo apt install aria2     # in main repos; ~v1.35+ on 20.04, newer on 24.04
  aria2c --version
  ```
- **Key commands CloudDog would use:**
  ```bash
  aria2c -x16 -s16 --dir=/tmp/dl https://example.com/file.zip        # segmented HTTP
  aria2c -c "magnet:?xt=urn:btih:..."                                 # resume-capable torrent
  aria2c --seed-time=0 --max-upload-limit=100K file.torrent            # leech, don't seed
  aria2c -i urls.txt                                                   # batch from file
  aria2c --enable-rpc --rpc-secret=TOKEN                               # daemon + JSON-RPC for orchestration
  ```
- **Drawbacks / reasons not to use:** It's a subprocess, not a library — progress parsing means scraping stdout or running the RPC daemon. No seeding/publishing story comparable to libtorrent. For pure HTTP pulls, `rclone copy` or `curl` may already suffice; aria2 earns its place on torrents, metalinks, and flaky big-file resumes.

---

## 4. Kaggle API — dataset upload automation

- **License:** Service (free Kaggle account), not software. `kaggle` PyPI package is Apache-2.0; newer `kagglehub` is also open-source.
- **What it does:** Scripted create/version/download of Kaggle datasets — CloudDog's free cold store.
- **Why it's relevant:** CloudDog already treats Kaggle datasets as a free store. The API makes "store a chunk, bump a version" fully unattended.
- **Install / auth:**
  ```bash
  pip install kaggle
  # then place API token at ~/.kaggle/kaggle.json (from kaggle.com → Settings → Create New Token), chmod 600
  ```
- **Key commands CloudDog would use:**
  ```bash
  kaggle datasets init -p ./my-dataset-dir          # writes dataset-metadata.json
  kaggle datasets create -p ./my-dataset-dir        # first upload
  kaggle datasets version -p ./my-dataset-dir -m "chunk batch 2026-10-17"
  kaggle datasets status <owner/dataset>            # processing status after version push
  kaggle datasets download <owner/dataset>          # pull-back
  ```
  ```python
  from kaggle.api.kaggle_api_extended import KaggleApi
  api = KaggleApi(); api.authenticate()
  api.dataset_create_version('./my-dataset-dir', version_notes='chunk batch 2026-10-17')
  ```
- **Limits (verified):** each Kaggle user has a **private-dataset quota of 100 GB+** (public datasets don't count against it). Upload is resumable/multipart under the hood. Versioning is append-only history — each `version` call is a new immutable snapshot.
- **Drawbacks / reasons not to use:** It's a data-science platform, not a backup service — ToS risk if used as pure blob storage; no SLA; version processing is async (uploads sit in "processing" before downloadable). Private quota is per-account and finite (~100 GB). Prefer public datasets for anything license-clean, private for the rest, and never as the *only* copy.

---

## 5. `gh` CLI — GitHub release management

- **License:** MIT (cli/cli). Free/open-source.
- **What it does:** Scriptable GitHub: releases, assets, attestations — CloudDog's primary store interface.
- **Why it's relevant:** Already central to CloudDog (chunk uploads, blob lanes). The verify step should use its asset-listing and attestation verification, not just size checks.
- **Install (Ubuntu/Debian):**
  ```bash
  sudo apt install gh            # in Ubuntu 22.04+ repos
  # or: official install script from cli.github.com
  gh auth login
  ```
- **Key commands CloudDog would use:**
  ```bash
  gh release create v2026.10.17 --title "clouddog chunks" --notes "..." part-*.bin
  gh release upload v2026.10.17 part-04.bin --clobber
  gh release view v2026.10.17 --json assets --jq '.assets[] | "\(.name) \(.size) \(.digest)"'
  gh release download v2026.10.17 --pattern 'part-*' --dir ./restore/
  gh release verify-asset <asset-file> --repo owner/repo     # proves asset came from the release
  sha256sum part-04.bin                                      # local digest to compare against manifest
  ```
- **Limits:** **2 GB hard cap per release asset** (CloudDog's 100 MB parts are fine). No cap on number of releases; repo-level soft guidance is ~100 GB total. Release assets do not count against git push limits.
- **Drawbacks:** None for this use case — it's the right tool. Just don't confuse *release assets* (right place) with *git blobs/LFS* (wrong place).

---

## 6. git-annex vs git-lfs — for 100 MB parts

### git-lfs
- **License:** MIT. Free/open-source.
- **What it does:** Replaces big files in git with tiny pointer files; the real content lives on an LFS server (GitHub's, or self-hosted). `git lfs track "*.bin"`, then normal `git add/commit/push`.
- **Relevance:** Lets large parts live *in the repo* with version history while keeping clones small.
- **Limits on GitHub:** **10 GB storage + 10 GB/month bandwidth included** on Free/Pro; overage is billed. Per-file up to 2 GB (5 GB on some plans).
- **Drawbacks for CloudDog:** Bandwidth quota burns on every clone/pull-back — a verify pass that downloads parts eats the 10 GB/mo budget fast. Content is versioned in the git tree, which CloudDog doesn't need for append-only chunks. **Verdict: usable, but worse than release assets.**

### git-annex
- **License:** GPL. Free/open-source.
- **What it does:** Replaces big files with symlinks to content-addressed storage (`.git/annex/`); supports arbitrary "special remotes" (S3, rsync, external drives). Distributed by design — no central server required.
- **Relevance:** In theory, the most CloudDog-shaped tool: content-addressed blobs, many backends, offline-friendly.
- **Drawbacks for CloudDog:** Steep learning curve; **not supported by GitHub/GitLab as a hosting feature** (repos with annexed files are opaque to the web UI); tooling is Haskell-based and operationally heavier; overkill for "store 100 MB parts and hash-verify them." **Verdict: do not adopt** — power without payoff for this pipeline.

**Bottom line for 100 MB parts:** keep doing what the blob lanes already do — **GitHub release assets** (2 GB/asset cap, no bandwidth quota games for the owner, `gh release view --json assets` gives name+size+digest for verify-then-delete). LFS is a fallback if parts ever need to be versioned *inside* the git tree. git-annex stays out.

---

## RECOMMENDATION (ranked)

### Tier 1 — wire up next
1. **Cloudflare R2 via rclone `s3` backend.** 10 GB/mo free + 1M/10M free ops + **$0 egress**. S3-compatible means CloudDog's existing S3 code path works unchanged. Best economics for a verify-then-reap loop that re-downloads for verification. (Needs a card on file — flag to owner.)
2. **Backblaze B2 via rclone.** 10 GB free **forever, no credit card**, $6/TB above that, egress free up to 3× stored. The safest "just works" free store; max file 10 TB. Use `b2` or `s3` backend — pick one and standardize.

### Tier 2 — useful additions
3. **aria2** for the download pipeline. One binary for HTTP/FTP/torrent/metalink, resume-safe, batch-friendly. Simpler than libtorrent for pull-back jobs.
4. **gh CLI verification hardening.** CloudDog already uploads via `gh`; add `gh release view --json assets` (name+size+digest) into the verify step and optionally `gh release verify-asset` for provenance. Zero new dependencies.
5. **MEGA via rclone** as an *extra* mirror only — 20 GB free and E2E, but transfer quotas make it unreliable as a primary.

### Tier 3 — situational
6. **libtorrent Python bindings** — only if CloudDog actually builds the torrent *mirror/seeding* mode (publishing torrents, not just downloading). If the lane only downloads, aria2 covers it.
7. **git-lfs** — only if a lane needs large files versioned inside the git tree. Not for chunk archiving.
8. **Kaggle API** — keep as-is (already in use); mind the ~100 GB private quota and the ToS/SLA caveats. Good cold tier, never the sole copy.

### Do not use
- **Storj** — no permanent free tier; $5/mo minimum billing floor makes it a money trap for a hygiene engine.
- **Dropbox / OneDrive** — 2 GB / 5 GB free tiers aren't worth the OAuth plumbing.
- **IDrive** — free-tier status is contradictory across 2026 sources; revisit only if the 10 GB Basic plan is confirmed live.
- **git-annex** — wrong complexity/benefit ratio for content-addressed chunk storage; hosting providers don't support it.
