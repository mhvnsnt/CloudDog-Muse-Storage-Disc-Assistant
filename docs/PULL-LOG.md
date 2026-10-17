# CloudDog PULL-LOG

Research survey of open-source pull-in candidates for CloudDog (disk as
cache: continuous mirror to permanent stores, byte-verify, then clear
local), conducted 2026-10-17 by the research lane. The draft below
(`/tmp/clouddog-research/PULL-LOG-draft.md`) is folded in verbatim —
per-project sections and verdicts kept as written.

## In-house components this package builds on

1. **cloud_dog.py** (PR #1, in-repo on `origin/main`) — stdlib-only
   verify-then-delete CLI: `scan` / `register` / `verify` / `reap` /
   `enforce`. Already wired into the disk-space-guardian cron. Registry:
   `~/workspace/agent-ops/cloud-dog-registry.json`. The `clouddog/` package
   extends it (TeraBox/Drive backends, policy engine, config, daemon,
   journal) rather than replacing it — see "Coexistence with cloud_dog.py"
   in `docs/OPERATIONS.md`.
2. **tbupload.py session-keeper path** (`~/workspace/tools/terabox-uploader/`,
   in-house) — the proven TeraBox upload flow (precreate, 4MB chunks,
   create, verify) plus the cookie session keeper. `clouddog/stores/terabox.py`
   implements this protocol in-process and reads the same
   `.creds.json`; CloudDog never handles raw cookie values.

---

# CloudDog PULL-LOG — Draft (research lane, 2026-10-17)

Read-only survey of real open-source projects relevant to CloudDog
(disk-as-cache: continuous mirror to stores, byte-verify, then clear local).
Survey rules honored: no installs, no clones of large repos, no code changes,
no process interference. Local LICENSE files were read where stated; where a
license could NOT be verified in this session it is marked explicitly.

## Top 5 pull-in candidates (summary)

1. **rclone** (MIT) — Drive + PikPak backends via subprocess. Has NO TeraBox
   backend (verified against the official backend list 2026-10-16). `rclone move`
   is the canonical copy-then-delete-only-on-success reference for CloudDog's
   reaper semantics.
2. **TeraboxUploaderCLI** (MIT, Python, on this VM) — vendor/adapt its Python
   chunked-upload + post-upload move/delete logic as the seed of CloudDog's
   TeraBox backend. Same language as CloudDog.
3. **gh CLI** (MIT) — shell out for GitHub release upload/download; use the
   published-SHA256SUMS + `sha256sum -c` verify pattern on download.
4. **aioterabox** (Apache-2.0, PyPI) — CANDIDATE pip dependency for the TeraBox
   backend; upload support NOT verified in this session, verify before pulling.
5. **Policy-engine references** — rclone `move` semantics, ncdu/duf UX patterns,
   tmpreaper age-threshold + dry-run concepts. Note: tmpreaper deletes WITHOUT
   verification, which violates CloudDog's laws — copy the policy ideas, not the
   delete behavior.

---

## 1. TeraBox API upload clients

### TeraboxUploaderCLI — dnigamer/TeraboxUploaderCLI
- URL: https://github.com/dnigamer/TeraboxUploaderCLI
- License: **MIT** — verified by reading the actual LICENSE file on this VM
  (`MIT License / Copyright (c) 2024 Gonçalo M.`).
- What it does: Python CLI uploader for TeraBox. Chunked upload (via curl),
  jsToken + session-cookie auth, optional client-side encryption of files
  before upload, `settings.json` with `movefiles` / `deletesource` options
  (post-upload move or delete), `ignoredfiles` glob list, quota display.
- Verdict: **PULL IN** — vendor/adapt the upload + post-upload-delete flow
  (`main.py` + `modules/`) as the seed of CloudDog's TeraBox backend. Python,
  so it fits CloudDog's stack. Keep the MIT LICENSE text + attribution.
  Caveats: (a) depends on curl (attempts to bootstrap it); (b) its credential
  flow is manual browser cookie capture — CloudDog's own session-keeper design
  (see `~/workspace/agent-ops/TERABOX-SESSION-KEEPER.md`) already covers the
  credential layer, so use the keeper for creds and this code for the transfer.

### terabox-complete-api — VinayakGhai/terabox-complete-api
- URL: https://github.com/VinayakGhai/terabox-complete-api
- License: **MIT** — verified by reading the actual LICENSE file on this VM
  (`MIT License / Copyright (c) 2026 Vinayak Ghai`).
- What it does: Node.js CLI (`upload.js`) + Cloudflare Worker that resolves
  the TeraBox `jsToken` server-side per request; chunked upload via
  `/api/precreate` + `/rest/2.0/pcs/file`; systemd user service; background
  detach; auto-heals expired `ndus` by re-reading the local browser SQLite
  store (`extract_browser_creds.py`). Credits `saahiyo/terabox-gateway` and
  `Pahadi10/terabox-upload-tool` for the proxy + chunk-upload patterns.
- Verdict: **EXCLUDE code pull-in; REFERENCE pattern.** Wrong stack for
  CloudDog (Node.js + requires deploying a Cloudflare Worker). The one pattern
  worth stealing is server-side jsToken resolution — which CloudDog can
  implement in Python instead of deploying a Worker.

### terabox-gateway — saahiyo/terabox-gateway
- URL: https://github.com/saahiyo/terabox-gateway
- License: **MIT** — per the GitHub repo metadata page (License: MIT License
  (MIT)); a LICENSE file exists in the repo root. The license text itself was
  not re-read this session.
- What it does: Flask-based gateway (also on PyPI as `terabox-gateway`) for
  extracting file metadata, thumbnails, direct download links and HLS streaming
  from TeraBox SHARE URLs, fronted by a Cloudflare Worker proxy. Download /
  share-link oriented.
- Verdict: **EXCLUDE** — wrong fit. CloudDog needs authenticated uploads, and
  this tool solves public share-link downloading. Its Worker-proxy architecture
  is already credited in terabox-complete-api; no code needed.

### aioterabox — PyPI package
- URL: https://pypi.org/project/aioterabox/
- License: **Apache-2.0** — per the PyPI project metadata captured by the
  search crawler. (The PyPI page itself failed to load in this session, so
  neither the license nor the feature list was directly re-verified.)
- What it does: async Python TeraBox client (`aiohttp`-based); the project's
  own PyPI "Thanks" section credits `seiya-npm/terabox-api` for the API
  reverse-engineering and `dnigamer/TeraboxUploaderCLI` for cookie-extraction
  instructions. Upload capability NOT confirmed this session.
- Verdict: **CANDIDATE** — if it supports authenticated uploads, add as a
  `pip` dependency for CloudDog's TeraBox backend (Apache-2.0 is
  pull-in-friendly). MUST verify upload support + maintenance status before
  depending on it. Do not vendor until verified.

### seiya-npm/terabox-api
- URL: (Node package `terabox-api`, repo seiya-npm/terabox-api)
- License: **MIT** — per the project's license documentation page; not
  re-read from the LICENSE file this session.
- What it does: Node.js client library for the TeraBox web API (auth, file
  ops, upload/download, sharing).
- Verdict: **EXCLUDE** — Node.js; CloudDog is Python. Python equivalents
  (TeraboxUploaderCLI, aioterabox) cover the same ground.

### basstimam/terabox-cli
- License: bundles **aria2, which is GPLv2+** (per the project's licensing
  documentation; the TeraBox-side code license not verified).
- What it does: Python TeraBox CLI/GUI; download side uses bundled aria2
  binaries; also has an experimental Tauri desktop component.
- Verdict: **EXCLUDE** — download-focused, and the aria2 GPLv2+ component is
  a copyleft risk if vendored into CloudDog. Download-side is out of scope for
  CloudDog's mirror-then-clear pipeline anyway.

---

## 2. rclone backends

### rclone — the project itself
- URL: https://rclone.org (backend list: https://rclone.org/tiers/)
- License: **MIT** — verified via GitHub API metadata (`"license":{"key":"mit",
  "spdx_id":"MIT"}`) and Wikipedia infobox (Licence: MIT). The LICENSE/COPYING
  file itself was not read this session (rclone is not installed on this VM).
- What it does: "rsync for cloud storage" — 70+ backends, `sync`/`copy`/`move`,
  `--dry-run` everywhere, checksum verification on transfer, mount, crypt.
- **TeraBox verdict: NO official backend exists.** Verified against the full
  official backend tier list at https://rclone.org/tiers/ (crawled 2026-10-16):
  the alphabetical list (Alias … Zoho) contains no TeraBox entry. So rclone
  CANNOT be CloudDog's TeraBox path — the dedicated Python uploaders (above)
  are required for TeraBox.
- Verdict: **PULL IN as a dependency/subprocess for the Google Drive and
  PikPak backends** (see below). Not for TeraBox.

### rclone `drive` backend (Google Drive)
- Backend tier: **Tier 1 (Core)**, per https://rclone.org/tiers/.
- Headless setup pattern (verified from https://rclone.org/remote_setup/):
  run `rclone config` on the headless machine, answer **N** to
  "Use web browser to automatically authenticate rclone with remote?", then on
  a browser-capable machine run `rclone authorize "drive"` and paste the
  returned token back. Service-account JSON (`service_account_file`) is an
  alternative for fully headless operation.
- Verdict: **PULL IN** — Drive backend of CloudDog = subprocess calls to
  `rclone copy/move/check drive:…`. Single small binary, MIT, battle-tested.

### rclone `pikpak` backend
- Backend tier: **Tier 1 (Core)**, external maintainer `wiserain`,
  per https://rclone.org/tiers/. Relevant because Real actively uses PikPak
  (per standing context: PikPak pulls are an approved TeraBox-alternative path).
- Verdict: **PULL IN** — same subprocess treatment as `drive` if CloudDog
  adds a PikPak store. Zero extra dependency beyond rclone.

### rclone `move` — the checksum-verified upload-then-delete reference
- Verified from https://rclone.org/commands/rclone_move/: "otherwise it will
  copy it (server-side if possible) into dest:path **then delete the original
  (if no errors on copy)** in source:path." And per rclone's documented
  behavior, transfers are checksum-verified; a failed/corrupt copy never
  triggers the source delete.
- Verdict: **REFERENCE THE PATTERN, do not reimplement blindly.** CloudDog's
  reaper must implement exactly this contract per file: hash local (sha256)
  → upload → fetch remote hash/size → compare → delete local ONLY on match →
  write audit-journal entry (sha256 + store location + timestamp). This is the
  behavioral spec for the policy engine's delete gate.

---

## 3. GitHub release upload/download tooling patterns

### gh CLI (`gh release create` / `gh release upload` / `gh release download`)
- License: MIT per the cli/cli project (not re-verified from the LICENSE
  file in this session).
- Verified patterns:
  - Create + upload in one shot: `gh release create v1.2.3 ./dist/*.tgz`
    (https://cli.github.com/manual/gh_release_create).
  - Upload to existing release: `gh release upload <tag> <files>...
    [--clobber]` — `--clobber` deletes same-named assets first; "If the
    upload fails, the original assets will be lost"
    (https://man.archlinux.org/man/gh-release-upload.1.en.raw). → CloudDog
    must NOT use `--clobber` on retry; verify-then-replace instead.
  - Download + verify: `gh release download "$TAG" --repo "$REPO"
    --pattern "$ASSET"` then `echo "${ASSET_SHA256}  asset.tar.gz" |
    sha256sum -c -` (verified community pattern). For CloudDog the sha comes
    from the manifest CloudDog itself published alongside the asset.
- Verdict: **PULL IN as a subprocess dependency** — CloudDog shells out to
  `gh` for the GitHub-releases store. No vendoring needed. Also publish a
  `SHA256SUMS` asset per release so any lane can verify downloads.

---

## 4. Google Drive upload tooling (headless)

Covered above via rclone: **rclone `drive` backend (Tier 1) + the headless
`rclone authorize "drive"` flow** is the recommended headless path. It is
MIT, single binary, supports `--drive-use-trash`, chunked uploads, and
`rclone check` for post-upload verification.
- Verdict: **PULL IN** (same dependency as §2). No separate Drive SDK needed;
  using the Drive REST API directly would just re-implement rclone.

---

## 5. Disk-usage / cache-manager tools with policy engines

### ncdu
- License: **MIT** — verified via the Debian manpage header
  (`SPDX-License-Identifier: MIT`, https://manpages.debian.org/unstable/ncdu/ncdu.1.en.gz);
  the ncdu 2.x rewrite (rcalixte/ncdu) is likewise MIT.
- What it does: interactive ncurses `du`; can export/import scans (`-o`/`-f`),
  exclude patterns, apparent-size vs disk-usage modes, in-TUI delete.
- Verdict: **REFERENCE** — steal the UX ideas (scan-export format, exclude
  flags, sort columns) for CloudDog's own scanner; shell out to ncdu as an
  optional diagnostic. No vendoring (it's C/Zig, CloudDog is Python).

### duf
- License: **MIT** — verified via the Ubuntu manpage ("Released under MIT
  license", https://manpages.ubuntu.com/manpages/jammy/man1/duf.1.html).
- What it does: `df` replacement with clean tables and **`--json`** output.
- Verdict: **REFERENCE / optional shell-out** — `duf --json` is a clean
  machine-readable source for CloudDog's disk-pressure watcher (80%/85%
  thresholds per the disk laws).

### gdu
- License: MIT (per third-party directory citing the GitHub repo; the
  LICENSE file itself was NOT read this session).
- What it does: fast Go disk-usage analyzer, console UI.
- Verdict: **REFERENCE only** — nothing it does is needed as a dependency;
  ncdu/duf cover the diagnostic needs.

### tmpreaper / tmpwatch (aged file reapers)
- License: NOT verified in this session (historically GPL — verify from the
  source before any vendoring).
- What it does: removes files not accessed for a threshold (`tmpreaper
  [-t|--test] <time_spec> <dirs>...`), atime-based, `--protect` shell
  patterns, `-t/--test` dry-run mode. The Debian manpage was read to confirm
  semantics (https://manpages.debian.org/unstable/tmpreaper/tmpreaper.8.en.html).
- Verdict: **REFERENCE the policy concepts only (age thresholds, protect
  patterns, dry-run-first), DO NOT copy the delete behavior.** tmpreaper
  deletes WITHOUT any verification that the data exists elsewhere — the
  exact opposite of CloudDog's upload→verify→THEN-delete law. Also note its
  symlink/race-condition hardening (`--symlinks`, no `chdir("..")`) as a
  checklist for CloudDog's own reaper.

### dust (bootandy/dust)
- License: NOT verified in this session.
- Verdict: **EXCLUDE / not needed** — duplicate functionality with ncdu/duf;
  unverified license; adds nothing CloudDog needs.

---

## 6. Checksum-verified upload-then-delete / "reaper" patterns

- **rclone `move`/`moveto`** (above) is the production reference implementation
  of exactly this pattern. CloudDog's reaper spec derived from it:
  1. Compute local sha256 BEFORE upload.
  2. Upload to store (TeraBox chunk API / rclone / gh).
  3. Read back the remote object's hash (or re-download the manifest entry /
     use the store's reported checksum) and compare to local sha256.
  4. Delete local ONLY on byte/hash match; on mismatch, keep local + log +
     alert (the in-house "notify on failure" pattern already exists in
     `kaggle_queue.py notify()` per standing memory).
  5. Append an audit-journal line: timestamp, path, sha256, size, store,
     store location/URL, verification result.
  6. `--dry-run` (CloudDog's default-on) prints what WOULD be deleted without
     touching anything — mirroring `rclone --dry-run` and `tmpreaper -t`.
- No standalone "hash-then-reap" library worth pulling was found; this logic
  is ~50 lines of Python and belongs in CloudDog itself, with the patterns
  above as the spec.

---

## What could NOT be verified (be honest)

- `aioterabox`'s actual upload support and maintenance status — PyPI page
  failed to load (client challenge); license (Apache-2.0) is from search
  metadata, not a LICENSE file read.
- `gh` CLI license text — MIT is widely documented but not re-read this
  session.
- `gdu`, `tmpreaper` license texts — not read this session.
- GitHub release asset size limits / rate behavior — not researched; the
  build lane must confirm before treating releases as a bulk store.
- Whether any newer native rclone TeraBox backend landed after the
  2026-10-16 crawl of https://rclone.org/tiers/ — the page is authoritative;
  re-check at build time.

## Local disk footprint of this survey

~0. Only file written: this PULL-LOG draft
(`/tmp/clouddog-research/PULL-LOG-draft.md`, a few KB). No pip installs, no
clones, no downloads, no code changes anywhere. Nothing outside
`/tmp/clouddog-research/` was touched.

---

## v2 pull-in candidates (owner standing order 2026-10-17 ~12:48 CDT)

Surveyed 2026-10-17 by the build coordinator (web sources, no installs).
Verdicts below; nothing here is wired into v1 — per the test-between-patches
order, each gets its own researched, tested patch.

### rclone — WIRED (v1)
- URL: https://rclone.org/ — License: **MIT** (verified against the official
  backend tier list, crawled 2026-10-16).
- Wired as the subprocess transport for the Drive store
  (`clouddog/stores/drive.py`: `rclone copyto` + `rclone lsjson` verify).
  No native TeraBox backend exists — TeraBox stays on the in-house
  `tbupload.py`-protocol backend.
- Future: PikPak backend is Tier 1 (external maintainer) — a `pikpak`
  CloudDog store is a clean v2 addition since the owner uses PikPak.

### dust (du + rust) — CANDIDATE (v2, not wired)
- URL: https://github.com/bootandy/dust — License: **Apache-2.0**
  (verified on the repo page 2026-10-17).
- What: parallel disk-usage walker; `dust -j` emits machine-readable JSON.
- Fit: real. `dust -j <root>` could become the fast path for
  `clouddog/core/inventory.py::scan()` on huge trees, with the current
  stdlib walker as fallback when the binary is absent.
- Wire plan (v2 patch): optional dependency, `shutil.which("dust")` probe,
  parse `-j` JSON into `Candidate` objects, tests with a fake `dust`
  executable on PATH. Not wired in v1 — no stacking untested work.

### fclones — CANDIDATE (v2, not wired)
- URL: https://github.com/pkolaczk/fclones — License: **MIT**
  (verified via linuxlinks + lib.rs 2026-10-17).
- What: fastest duplicate-file finder; `fclones group --format json`,
  `--dry-run` on every mutating subcommand — semantics match our laws.
- Fit: real. Duplicate bulk is the purest waste on a 98% disk: a future
  `clouddog dedupe --dry-run` command would consume `fclones group` JSON,
  keep one copy per policy, and route the rest through the normal
  verify-then-clear pipeline.
- Wire plan (v2 patch): subprocess integration like rclone/gh, guarded by
  the policy engine (protected + in-progress rules apply to every
  candidate). Not wired in v1.

### sqlite — CANDIDATE (v2, not wired)
- License: **public domain**; stdlib `sqlite3` — zero new dependencies.
- Fit: real. The JSONL journal (`clouddog/core/manifest.py`) is correct for
  v1; if it grows past comfortable grep/scan size, a sqlite journal
  backend (same `Journal` interface, `journal_path` ending in `.db`)
  gives indexed queries over store history.
- Wire plan (v2 patch): alternative `Journal` backend + migration
  (`journal --migrate jsonl→sqlite`), tested round-trip. Not wired in v1.

### libtorrent — EVALUATED, NOT PULLED (weak fit)
- URL: https://github.com/arvidn/libtorrent — License: **BSD-3-Clause**
  (verified via openSUSE spec + Wikipedia 2026-10-17).
- Honest reason: license-clean but wrong tool for this mission. CloudDog
  moves bytes disk→cloud (TeraBox/GitHub/Drive); nothing in the law set
  asks for P2P distribution, and libtorrent is a C++/Boost native
  dependency — heavy to build, heavy to test, ~0 payoff for v1/v2 scope.
  Revisit only if a torrent-based *acquisition* lane (PULL-STRAIGHT-TO-STORE
  for the gaps-first pulls) ever becomes a real requirement.
