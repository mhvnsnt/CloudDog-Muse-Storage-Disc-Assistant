# Research — CloudDog Fetch (2026-10-21)

Owner directive (2026-10-20): replace TorBox with a free service or our own
open-source stack inside CloudDog. This document lands the Phase 1 research
in the repo (RESEARCH-TO-REPO law) and records the decisions it drove:

**Decisions taken from this research**
1. No free debrid service is viable for the flagship workload (Part A
   verdict: largest free cap 10 GB vs a 350 GB single-file torrent, and
   torrent-level file selection cannot see inside a zip). Do not plan
   around any free/trial debrid account for this job.
2. Engine = libtorrent 2.x via Python bindings — the only candidate with
   per-piece dont_download from Python (Part B §1). aria2/qBittorrent-nox/
   Transmission are file-level only and degenerate to all-or-nothing.
3. Storage = gh CLI for GitHub Releases (no rclone backend exists);
   rclone for Drive/B2/S3 mirror targets (Part B §3). Entries >= 2 GiB
   must be split for GitHub (per-asset limit).
4. ZIP layer = our own ZIP64-aware central-directory parser (adapted from
   the TorBox lane's proven zip_extract_parallel.py), gated on piece
   presence, CRC32+size verified per entry (Part B §4-5).

Part A: free debrid alternatives survey. Part B: self-hosted stack.

---

# Debrid / Cloud-Torrent Alternatives to TorBox — Research for CloudDog Fetch (Phase 1a)

**Accessed:** 2026-10-20/21 CDT (overnight). All numbers below are from the cited pages as fetched on that date. Anything I could not pin to a source is marked **UNVERIFIED** — do not treat it as a fact.

**The exact workload:** selectively extract ~77 files (~4.6 GB total) out of ONE torrent whose entire payload is a **single 350 GB file (a .zip)**, via API, without downloading the whole 350 GB. TorBox now plan-gates the owner's API key; owner declines to pay.

---

## The two-layer problem (read this first — it decides everything)

1. **Torrent-level file selection does not apply here.** "Select files" features (Real-Debrid `selectFiles`, TorBox, Seedr, WebTorrent `file.select()`, etc.) choose among the files *listed in the torrent metadata*. This torrent lists exactly **one** file: the 350 GB zip. Selecting it = downloading 350 GB. There is no torrent-level way to pick the 77 files *inside* the zip.
2. **Inside-the-zip extraction on a debrid service requires the whole zip on their cloud first.** A service would have to (a) accept and fully download the 350 GB torrent into your cloud storage, then (b) open the zip and serve individual entries. So the quota that matters is **350 GB of cloud/per-download capacity, not 4.6 GB**. No free tier in this survey offers more than 10 GB (see table). Seedr is the only service found that explicitly advertises browsing inside archives ("open them like a regular folder — browse inside and download individual files"), and only *after* the archive has fully transferred into storage you pay for by size.
   - Sources: Seedr Android listing, https://www.appbrain.com/app/seedr/com.seedr.app ; Real-Debrid API `POST /torrents/selectFiles/{id}` description, https://pkg.go.dev/github.com/divyam234/go-debrid/realdebrid
3. **The ZIP format itself is friendly to a self-built selective fetch** (relevant to Phase 1b, not to any service below): a zip's central directory sits at the *end* of the file and records each entry's offset, compressed size, and method; entries are normally compressed individually (stored/deflate), not solid. So a torrent client with piece-level prioritisation can fetch only the pieces covering the central directory + the wanted entries' byte ranges (~4.6 GB plus piece-boundary overhead). This is a property of ZIP + BitTorrent pieces — no debrid service surveyed sells it as a feature.
   - Source: ZIP structure (local headers / central directory / end-of-central-directory, central header field "relative offset of local header"), https://ctf-wiki.mahaloz.re/misc/archive/zip/

---

## Service-by-service

### TorBox (incumbent) — torbox.app
- **Free tier (official pricing page):** $0 — 1 concurrent slot, 24-hour cooldown, 10 downloads/month, **10 GB max per download**, 250 Mbps, reduced traffic priority. The pricing page text lists "API Access" on Free — **this contradicts the owner's lived experience** (API key returns plan-gated AUTH_ERROR / `PLAN_RESTRICTED_FEATURE`). Flagged discrepancy: marketing page vs. actual API behaviour; owner's experience is the ground truth for this project.
- **Paid:** Standard **$5/mo** — 200 GB max per download, 500 GB permanent storage. Pro **$10/mo** — **1 TB max per download (marketing)**, 1 TB permanent storage, 10 slots. Note: the TorBox API error table reproduced in the `torbox-api` PyPI package lists the Pro per-download limit as 536,870,912,000 bytes = **500 GB**, not 1 TB — either figure is >350 GB, so Pro is the *only* TorBox tier that accepts this torrent either way. (Older 2026 reviews list an "Essential $3/mo, 200 GB limit" tier that no longer appears on the official pricing page — **UNVERIFIED / apparently discontinued or renamed**.)
- **File selection:** torrent-level yes; in-cloud unzip / inside-zip browsing **UNVERIFIED**.
- **Persistence:** best in class on paper — "permanent storage" 500 GB (Standard) / 1 TB (Pro).
- Sources: https://torbox.app/pricing ; https://pypi.org/project/torbox-api/1.0.0/ ; https://www.geekextreme.com/torbox-review/ ; https://troypoint.com/real-debrid-alternatives/ (Free: 10 downloads/month, 10 GB max)

### Real-Debrid — real-debrid.com
- **Free tier:** none usable. You can register free, but premium features require payment; the old free "happy hours" are reported discontinued (guides could not confirm any free access with Real-Debrid itself). No free API usage for torrents in practice.
- **Paid (official/guide pricing):** **€3 / 15 days** (cheapest entry in this entire survey), €4 / 30 days, €9 / 90 days, €16 / 180 days.
- **Torrent acceptance:** a secondary guide states **max torrent size 2,000 GB**, max torrent download duration 72 h, max 25 torrent conversions at once — so 350 GB is accepted on size alone. **Treat the 2,000 GB figure as secondary-sourced, not official.**
- **API + file selection:** yes, and the best-documented of the group: `POST /torrents/selectFiles/{id}` starts downloading only the selected files of a multi-file torrent. For this job it selects the single zip — i.e. all 350 GB, delivered as a direct download link (no persistent cloud storage / in-cloud unzip in the model).
- **Persistence:** generated download links reported valid **30 days** (secondary source).
- Sources: https://www.firesticktvstream.com/what-is-real-debrid/ ; https://koditips.com/real-debrid/ ; https://citizenside.com/technology/what-is-real-debrid-how-to-use-kodi/ ; https://pkg.go.dev/github.com/divyam234/go-debrid/realdebrid

### AllDebrid — alldebrid.com
- **Free tier:** no standing free tier. **7-day free trial** for new accounts, SMS-verified, under a fair-use policy (not unlimited). Trial API access and trial torrent-size/volume caps: **UNVERIFIED**.
- **Paid (official offer page):** €2.99 / 30 days recurring (card only), or one-time €2.99 / 15 days, €3.99 / 30 days, €8.99 / 90 days, €15.99 / 180 days, €24.99 / 300 days.
- **Torrent acceptance:** magnets supported via API (`api.alldebrid.com`, magnet upload + `magnet/files` listing). **Max torrent size: UNVERIFIED** — no official number found. Documented integrations note AllDebrid's flow has **no file-selection step** (unlike Real-Debrid): the magnet downloads, then its files are listed.
- **Persistence of a downloaded 350 GB torrent in-account: UNVERIFIED.**
- Sources: https://alldebrid.com/offer ; https://troypoint.com/best-debrid-services/ ; https://deepwiki.com/debridmediamanager/debrid-media-manager/3.1-torrent-management ("AllDebrid uses a simpler flow without file selection")

### Premiumize — premiumize.me
- **Free tier:** none usable — free registration exists, a paid membership is required to actually use the service.
- **Paid (official):** **€9.99 / month** (US$11.99) or €69.99 / year. Every plan includes **1 TB cloud storage** and a **Developer API**.
- **Traffic model (official help centre):** 1,000 fair-use points max, **1 point = 1 GB of traffic**, +30 points/day, refilled to 1,000 on purchase. A 350 GB torrent download ≈ 350 points — fits comfortably; cloud storage itself is not charged against points.
- **File selection / inside-zip:** torrent files land in your 1 TB cloud and are managed as files/folders; an in-cloud "extract only these zip entries" feature is **UNVERIFIED**.
- **Persistence:** files stay in cloud until you delete them per current guides (one older guide mentions a 30-day auto-delete — **conflicting, UNVERIFIED**).
- Sources: https://premiumize.me/premium ; https://premiumize.reamaze.com/kb/technical/download-limit ; https://koditips.com/premiumize-kodi-info-review-setup/

### Debrid-Link — debrid-link.com
- **Free tier:** described in French listings as "partiellement gratuit" (partly free), but **no free-tier quota, torrent size, or API terms could be verified** from its official pages.
- **Paid (official):** €3 / 15 days, €4 / 30 days, €9 / 90 days, €16 / 180 days, €25 / 300 days — includes seedbox downloading/seeding and all 41 hosts.
- **API:** a v2 API exists (`debrid-link.com/api/v2`, per third-party integration docs) — paid-tier presumably; free-tier API **UNVERIFIED**.
- **Max torrent / seedbox storage size: UNVERIFIED** — the official pricing page publishes no storage or per-torrent number, so 350 GB acceptance cannot be confirmed.
- Sources: https://debrid-link.com/premium ; https://deepwiki.com/sirrobot01/debrid-blackhole/3.1-debrid-services-configuration

### LinkSnappy — linksnappy.com
- **Free tier:** a limited free account is reported by secondary sources (restricted volume/speeds) — quotas **UNVERIFIED** from official sources.
- **Paid (secondary):** $4.99 / 7 days, **$12.99 / 30 days**, $29.99 / 90 days — the most expensive mainstream option per month here.
- **Torrents:** cloud torrent support is claimed in secondary write-ups; **max torrent size, cloud storage quota, API availability, and file selection are all UNVERIFIED**. Nothing found suggests it could hold a 350 GB file.
- Sources: https://koditips.com/real-debrid-vs-premiumize-vs-all-debrid-vs-linksnappy/ ; https://www.toolsurf.com/seo-tools/linksnappy-group-buy/

### Seedr — seedr.cc (cloud torrent service)
- **Free tier (official):** **2 GB storage**, expandable to ~6 GB via referral/task bonuses (secondary). This storage cap *is* the max torrent size — a torrent must fit in your storage.
- **API on free:** yes but basic — Seedr's own automation page: "Free plans have basic API access with lower rate limits… Premium plans unlock higher rate limits." WebDAV requires Master+, FTP Basic+.
- **Paid (official pricing):** Lite $3.95/mo = 10 GB; Basic $7.95/mo = 50 GB; Pro $12.95/mo = 500 GB; Master $19/mo = 1 TB. Only Pro/Master could even *hold* the 350 GB zip.
- **Inside-zip:** the one bright spot — Seedr advertises automatic archive handling: "Once finished, open them like a regular folder — browse inside and download individual files or specific subfolders." But that is after the **whole** archive is in your (paid, ≥500 GB) storage.
- **Persistence:** files live in your cloud storage until you delete them (no expiry found; long-term retention terms **UNVERIFIED**).
- Sources: https://www.seedr.cc/pricing ; https://www.seedr.cc/products/travel/ (Free = 2 GB) ; https://www.seedr.cc/products/automation/ ; https://www.appbrain.com/app/seedr/com.seedr.app

### Bitport — bitport.io (cloud torrent service)
- **Free tier (secondary sources agree closely):** **1 GB storage** (a few older sources say 2 GB — conflicting), 1 torrent/day, 1 slot, throttled speed, no SSL, no antivirus scan.
- **Paid (secondary):** $5/mo = 30 GB, $10/mo = 100 GB, $15/mo = 250 GB. **Even the top paid tier (250 GB) is smaller than the 350 GB torrent.**
- **API:** no public API documented anywhere found — treat as **none / UNVERIFIED**.
- Sources: https://omghowto.com/best-cloud-torrenting-service-providers/ ; https://hubprix.com/bitport-best-cloud-torrent-privacy-fastest/ ; https://technofaq.org/posts/2016/07/hassle-free-torrenting-at-work-or-university-using-bitport/

### Offcloud — offcloud.com (cloud download manager)
- **Free tier:** conflicting reports — one review says free account = 3 links / up to 10 GB; an old Chrome Web Store listing says a free starter with up to 100 links including 2 BitTorrent sources. **Current free terms: UNVERIFIED.** (The service was reportedly rebuilt under new ownership.)
- **Paid:** current guides list **$4.99 / 30 days** and $39.99 / 365 days, with a "free trial" of up to 30 days claimed by one guide — trial terms **UNVERIFIED**. Historical lifetime deals included 50 GB cloud space and API access.
- **Max torrent size / storage on current plans: UNVERIFIED.** No evidence found that any current plan holds 350 GB.
- Sources: https://troypoint.com/best-debrid-services/ ; https://www.ki-wi.co.nz/offcloud-review/ ; https://chromewebstore.google.com/detail/offcloud-for-drive/nmaijleinoonghaenmjibfhbldeobllp ; https://www.gamespot.com/articles/offclouds-cloud-based-download-manager-is-a-unique-and-useful-tool/1100-6504649/

### Put.io (for completeness)
- No permanent free tier. Paid storage plans (secondary): 100 GB $9.99/mo, 1 TB $19.99/mo — only the 1 TB tier holds the zip, at the highest price here. Has a real API, but irrelevant at $0 and not cheapest if paying.
- Source: https://www.picbackman.com/tips-tricks/8-best-cloud-torrent-service-providers/

### 2026 newcomers / free-claim services
- **World-Debrid** — the 2026 newcomer pushed by ranking sites (€2.79/mo premium, "genuine free plan, no card"). **Its own comparison page admits the free plan is limited to 1 debrid action per month.** Torrent max size, API on free, and file selection: **UNVERIFIED**; brand is new with a small cached pool per the same ranking sites. One action/month cannot drive a 77-file API workflow, and nothing verifies 350 GB acceptance.
  - Sources: https://world-debrid.com/en/meilleur-debrideur-gratuit ; https://debridrank.com/
- **DeepBrid / CocoLeech / NeoDebrid-style free link generators** — free, but they are hoster-link generators; no verified torrent-cloud storage, no verified API, no plausible 350 GB path. Not candidates.
  - Source: https://technofizi.net/sitelike/alldebrid.com
- **TorrentSafe** — freemium cloud torrent: free = 3 torrents/month up to **10 GB**, files kept ~2 days (secondary). Same 10 GB wall as TorBox free.
  - Source: https://quickfever.com/best-cloud-torrenting-sites
- **Pulsed Media free seedbox** — a free seedbox tier exists ("evaluation use"); storage/spec **UNVERIFIED** and seedboxes are not debrid APIs. Not a candidate without a spec check.
  - Source: https://wiki.pulsedmedia.com/index.php?title=Seedbox&diff=cur&oldid=28&printable=yes

### WebTorrent / open-source torrent engines (not a service — the build-your-own lane)
- **WebTorrent** has genuine selective primitives: `file.select()` / `file.deselect()`, and `file.createReadStream({start, end})` to stream a byte slice of a single file — in principle exactly the primitive a zip-range fetcher needs. **Catch:** browser WebTorrent speaks WebRTC and cannot talk to the standard TCP/uTP swarm by itself (needs WebRTC-capable peers or a hybrid/Node client); the Node/desktop engine or libtorrent-family clients (qBittorrent, Transmission, aria2) reach the normal swarm and support per-file priorities — but per-*file*, not per-zip-entry, so the zip-range/piece-priority logic has to be built (that's the CloudDog Fetch Phase 1b premise, and ZIP's end-of-file central directory is what makes it feasible, see §"two-layer problem").
- Sources: https://inchoong.github.io/webtorrent/docs/api.html ; https://gulfrilo.weebly.com/blog/archives/05-2023 (WebRTC peer limitation)

---

## Direct answers

**Q: Do debrid services accept a 350 GB torrent on *any* tier?**
Yes — a few, all paid:
- TorBox **Pro** ($10/mo): 1 TB max per download per marketing; 500 GB per the API error table. Either way >350 GB. Standard/Essential (200 GB) and Free (10 GB) reject it.
- Real-Debrid (paid): 2,000 GB max torrent per secondary guide.
- Premiumize (paid): 1 TB cloud storage + 1,000 GB-equivalent fair-use points — the zip fits its cloud.
- Seedr Pro/Master ($12.95/$19): 500 GB / 1 TB storage — fits, barely (Pro).
- Bitport: **no tier fits** (max 250 GB). AllDebrid, Debrid-Link, LinkSnappy, Offcloud: **UNVERIFIED** — no published number found, so acceptance cannot be claimed.

**Q: Can you pull individual files from inside a single-file archive torrent?**
- Via torrent file-selection: **No, nowhere.** There is only one file in the torrent to select.
- Via in-cloud archive browsing after a full download: **Seedr explicitly yes** (paid ≥500 GB storage tiers); TorBox and Premiumize in-cloud unzip of a 350 GB zip: **UNVERIFIED**. Real-Debrid/AllDebrid hand you the zip (or its download link) — extraction would happen on your machine, i.e. you'd have to pull the 350 GB down. No service surveyed offers "fetch only these entries from inside the zip" as a feature — that capability only exists as a build-it-yourself technique (piece prioritisation + ZIP central directory).

**Free-tier ceiling summary:** TorBox Free 10 GB · TorrentSafe Free 10 GB · Seedr Free 2 GB · Bitport Free 1 GB · World-Debrid Free 1 action/month (size UNVERIFIED) · Offcloud Free ≤10 GB (UNVERIFIED/conflicting) · Real-Debrid / AllDebrid / Premiumize / Debrid-Link / LinkSnappy: no usable free tier (AllDebrid's 7-day SMS trial is the only free *window* into a full service, with its torrent caps UNVERIFIED). Every free ceiling is **35× or more below** the 350 GB the torrent demands before any inside-zip extraction can even start.

---

## VERDICT

**Is any FREE service viable for this exact workload? NO.**

Three independent reasons, any one of which is fatal:
1. **Size:** the job needs a service to swallow a 350 GB single file before it can extract anything from inside it. The largest verified free per-download/storage cap found is **10 GB** (TorBox Free, TorrentSafe Free) — 35× too small. Seedr Free (2 GB) and Bitport Free (1 GB) are worse.
2. **Selection model:** every "select files" API in this space selects among a torrent's files. This torrent has one file. The 77 wanted files live *inside a zip*, and no free service offers in-cloud zip-entry extraction at all (the one service advertising archive browsing, Seedr, does it only after a full transfer into paid ≥500 GB storage).
3. **API/automation on free tiers:** TorBox Free's API is plan-gated in practice (owner's AUTH_ERROR, despite the pricing page listing API access); Seedr Free API exists but is storage-capped at 2 GB; World-Debrid Free allows 1 action/month; AllDebrid's only free path is a 7-day SMS-verified trial whose torrent size/volume limits are UNVERIFIED — it is the single free loophole *worth one test* if a test is ever wanted, but on current evidence it cannot be called viable.

**Cheapest paid option IF the owner ever chose to pay (research only — nothing purchased):**
- **Cheapest sticker:** Real-Debrid **€3 for 15 days** (AllDebrid ties at €2.99/15 days one-time or €2.99/mo recurring, but its max torrent size is UNVERIFIED). Real-Debrid's documented 2,000 GB torrent cap (secondary) and `selectFiles` API fit the size — *but* it delivers the whole zip as a download with no cloud storage/unzip, so it does not by itself reproduce the TorBox workflow.
- **Cheapest *verified complete fit* for the original workflow:** **TorBox Pro, $10 for one month** — the only sub-€10-ish plan with both a verified >350 GB per-download cap (1 TB marketing / 500 GB API table) *and* ≥350 GB permanent storage, using the API shape the existing CloudDog/TorBox tooling already speaks. Premiumize at €9.99/mo (1 TB cloud + Developer API + enough fair-use points) is the runner-up and the better fit if a generic cloud + API is preferred over TorBox specifically.

**Implication for CloudDog Fetch:** the free-service lane is a dead end for this job — the numbers aren't close. The viable free path is the owner's own stack (open-source torrent engine + piece-level selective fetch driven by the ZIP central directory + rclone/mirror storage), which is precisely the Phase 1b build. No debrid account, free or trial, should be planned around for the 350 GB job.

---

# CloudDog Fetch — Self-Hosted Stack Research (Phase 1b)

Date: 2026-10-21 (all web sources accessed 2026-10-21)
Workload pinned by the task: **one 350 GB single-file torrent (one big ZIP)**; selectively fetch
only (a) the ZIP end-of-central-directory / central directory at the tail and (b) ~77 entries'
data, ≈4.6 GB total; never the whole file; then push verified extracted files to GitHub
Releases / Google Drive / Backblaze B2 and register mirror URLs.
Local environment verified on this VM 2026-10-21: Python 3.12, `libtorrent 2.1.1.0` importable,
~13 GB free on `/` (task says ~14 GB — treat disk as hard-tight).

---

## 1. Torrent engine for byte-range / piece-selective download

### 1.1 The core requirement, restated

File/torrent selection is **not enough**. In a single-file torrent there is exactly one
"file," so every client's file-selection UI/API degenerates to all-or-nothing. The only
mechanism that can fetch "the tail + 77 scattered byte ranges inside that one file" is
**per-piece priority: set every piece to `dont_download` (0) except an explicitly computed
wanted set.** Only one candidate exposes that programmatically.

### 1.2 libtorrent (Rasterbar) 2.x — YES, piece-level, from Python ✅

Official docs:

- Piece priorities: "There are 8 priority levels. **0 means not to download the piece at
  all.** Otherwise, lower priority values means less likely to be picked… By default all
  pieces have priority 4." APIs: `piece_priority(index, prio)`, `prioritize_pieces(vector)`,
  `get_piece_priorities()`. Caveat quoted verbatim: "**Piece priorities can not be changed
  for torrents that have not downloaded the metadata yet.** Magnet links won't have metadata
  immediately. see the `metadata_received_alert`."
  — https://www.rasterbar.com/products/libtorrent/reference-Torrent_Handle.html (accessed 2026-10-21)
- Piece picker internals confirm this is the designed selective-download path: "Pieces with
  priority 0 will never be picked, which is used for the selective download feature."
  — https://www.rasterbar.com/products/libtorrent/features-ref.html (accessed 2026-10-21)
- Byte-range → torrent mapping helper exists in the API itself: `torrent_info::map_file(file,
  offset, size)` "will map a range in a specific file into a range in the torrent" (returns a
  `peer_request`: piece + start + length), and `map_block(piece, offset, size)` for the
  inverse. — http://libtorrent.org/reference-Torrent_Info.html (accessed 2026-10-21).
  For a single-file torrent the arithmetic is trivial anyway (see §5), but `map_file` is the
  authoritative cross-check.
- Magnet metadata + trackerless operation, features list: "supports trackerless torrents
  (using the Mainline kademlia DHT protocol)… **BEP 5**", "supports the uTorrent metadata
  transfer protocol **BEP 9** (i.e. magnet links)", BEP 10 extension protocol, BEP 11 PEX,
  BEP 12 multi-tracker, BEP 53 (magnet file-index selection).
  — https://www.rasterbar.com/products/libtorrent/features-ref.html (accessed 2026-10-21)
- Disk footprint: `storage_mode_sparse` — "All pieces will be written to the place where they
  belong and sparse files will be used. **This is the recommended, and default mode**";
  `storage_mode_allocate` pre-allocates full files (fatal here: it would try to lay down
  350 GB). — http://libtorrent.org/reference-Storage.html (accessed 2026-10-21)

**Locally verified on this VM (2026-10-21, `libtorrent 2.1.1.0` Python bindings):**

- `lt.torrent_handle.prioritize_pieces`, `.piece_priority`, `.get_piece_priorities`,
  `.prioritize_files`, `.have_piece`, `.read_piece`, `.set_piece_deadline` all present.
- `lt.add_torrent_params` exposes `piece_priorities`, `file_priorities`, `storage_mode`,
  `flags`, `ti`, `save_path` — i.e. priorities can be seeded at add time when a `.torrent`
  file (not just a magnet) is in hand.
- `lt.torrent_flags.default_dont_download` exists, plus `paused`, `stop_when_ready`,
  `disable_dht` flags. Practical pattern: add paused / `default_dont_download`, wait for
  `metadata_received_alert` (magnet case), then `prioritize_pieces([...0 except wanted...])`
  and resume. This matches the doc caveat that priorities cannot be set pre-metadata.
- `lt.storage_mode_t.storage_mode_sparse` is available.

Integrity bonus: libtorrent itself hash-verifies every downloaded piece against the
torrent's piece hashes (SHA-1 for v1; SHA-256 for v2) before marking it `have_piece` — so
downloaded bytes are verified before any ZIP parsing trusts them.

### 1.3 aria2 — NO piece exclusion ❌

- Its only BT selectivity is **file-index** selection: `--select-file=1-4,8`, "Set file to
  download by specifying its index… In multi file torrent, the adjacent files specified by
  this option may also be downloaded… A single piece may include several files."
  In a single-file torrent the only index is `1` → selects everything.
  — https://manpages.ubuntu.com/manpages/bionic/en/man1/aria2c.1.html and
  https://manpages.debian.org/unstable/aria2/aria2c.1.en.html (accessed 2026-10-21)
- The only piece-level knob is `--bt-prioritize-piece=head[=<SIZE>],tail[=<SIZE>]` — it
  *prioritises* first/last pieces of each file "useful for previewing files." It does **not**
  exclude any piece; aria2 will still eventually download the whole 350 GB.
  — same manpages above (accessed 2026-10-21)
- Magnet/DHT are fine (so it fails on selectivity, not on reach): aria2 downloads metadata
  from peers via BEP 9/`ut_metadata` and "implements the Mainline DHT (BEP 5) for
  trackerless peer discovery, supporting both IPv4 and IPv6"; also `--bt-metadata-only` /
  `--bt-save-metadata`. — https://deepwiki.com/aria2/aria2/3.3-bittorrent-implementation
  (accessed 2026-10-21)
- No piece-want/piece-priority option appears anywhere in the full aria2c option list in the
  manpages above (negative finding from the documented option set, 2026-10-21).

### 1.4 qBittorrent-nox — NO (file-level only, via WebUI API) ❌

- qBittorrent is built on libtorrent, but its exposed control surface is the WebUI API.
  The Python client documents `torrents_file_priority(torrent_hash, file_ids, priority)` —
  "**Set priority for one or more files**" — priorities are per *file ID*, with values
  skip/normal/high/max in the underlying API; there is a read-only piece-state/piece-hashes
  surface but no API to set piece priorities.
  — https://qbittorrent-api.readthedocs.io/_/downloads/en/latest/pdf/ (accessed 2026-10-21)
- Consequence: for our one-file torrent, setting that file to "skip" downloads nothing and
  "download" downloads 350 GB. Running a whole extra daemon (nox + WebUI auth/session) to
  get *less* control than the library underneath it is pure overhead.

### 1.5 Transmission — NO (file-level only, via RPC) ❌

- RPC spec: `torrent-add` / `torrent-set` accept `files-wanted`, `files-unwanted`,
  `priority-high|low|normal` — all **arrays of file indices**.
  — https://github-wiki-see.page/m/Beatlegger/Transmission.API.RPC/wiki/Transmission-RPC-Specification
  (accessed 2026-10-21)
- Newer RPC (v18 / Transmission 4.1) adds per-file `begin_piece` / `end_piece` fields — but
  those are *reported metadata* in `torrent-get`, not a way to want/unwant individual pieces.
  — https://deepwiki.com/trim21/transmission-rpc/4.3-type-system (accessed 2026-10-21)
- Same degeneracy as qBittorrent: one file ⇒ all or nothing. DHT/magnet support exists in
  Transmission generally, but is moot given no piece control. **UNVERIFIED** in this pass:
  a primary Transmission doc page stating its BEP 9 support explicitly — not needed for the
  verdict because it already fails the selectivity test.

### 1.6 Byte-range → piece-index mapping (engine-independent math)

For a single-file torrent with `piece_length = P` and total size `S`:

```
num_pieces        = ceil(S / P)
pieces_for_range(start, length):            # length >= 1, range is [start, start+length)
    first = start // P
    last  = (start + length - 1) // P
    return range(first, last + 1)
wanted = union over all wanted byte ranges of pieces_for_range(...)
all other pieces -> priority 0 (dont_download); wanted -> priority 7 (top) or 4 (normal)
```

Costs to budget: every disjoint wanted range **rounds outward to piece boundaries**,
adding < 2·P of overhead per range. P for a 350 GB torrent is commonly large
(4–16 MiB is typical for torrents of this size); with ~79 disjoint ranges (EOCD/CD + 77
entries, plus local-header probes) worst-case overhead is < 79 × 2P — at P=16 MiB that is
up to ~2.5 GB of extra pieces in the pathological every-range-isolated case, less when
entries cluster and share pieces. **UNVERIFIED:** this specific torrent's `piece_length`,
piece count, v1 vs v2/hybrid, and whether it even has working peers/trackers/DHT presence —
all are knowable only from its `.torrent`/magnet metadata in Phase 2, and the budget must
be recomputed from the real P before downloading. If P turns out to be huge and entries are
maximally scattered, wanted-bytes + overhead must still fit the ~13 GB disk alongside
extracted output (see §4.4) — process entry-at-a-time to stay safe.

---

## 2. How real debrid services structure fetch → store → mirror-URL

Public sources describe the *shape* consistently; internals of Real-Debrid/TorBox are
proprietary, so anything below the API surface is marked accordingly.

1. **Cache-first, keyed by infohash.** "the debrid services keep content on their servers
   downloaded by any user, or download it if it was not requested before… When someone else
   requests the same content, they just serve it without downloading again which is the
   cache." — http://techenclave.com/t/services-like-torbox-real-debrid-seedbox-etc/431949
   (post dated 2026-07-05, accessed 2026-10-21). Real-Debrid exposes this as an API:
   `/torrents/instantAvailability` with batched hashes (up to 50 at a time).
   — https://deepwiki.com/rattin-player/rattin-public/3.4-debrid-integration (accessed 2026-10-21)
2. **Seedbox layer (cache miss).** "Here the service behaves like a simplified seedbox. It
   takes the magnet or torrent, downloads it in a data center, and then delivers it to the
   user over HTTP or streaming. If the torrent is already cached, the result is instant."
   — https://pablomurad.com/how-debrid-services-work/ (accessed 2026-10-21). Same point,
   other wording: services download "at datacentre speeds" and "temporarily store it… so
   you or anyone accessing same torrent get a direct https link to download from their CDN."
   — https://techenclave.com/t/beginners-guide-to-debrid-usenet-content/446270 (accessed 2026-10-21)
3. **State machine via API (this is the part CloudDog Fetch should copy).** Documented
   Real-Debrid client flow: `addMagnet` → torrent sits in `waiting_files_selection` →
   `selectFiles` (choose file IDs) → `downloading` → `downloaded`, exposing per-file
   `links[]` → `unrestrictLink` / `POST /unrestrict/link` converts a link into a direct,
   CDN-served download URL. — https://deepwiki.com/tim-smart/stremio-effect/4.4-premium-services,
   https://deepwiki.com/westsurname/scripts/4-shared-infrastructure,
   https://deepwiki.com/itsrenoria/robofuse/4.4-real-debrid-integration (all accessed 2026-10-21)
4. **Mirror-URL = a second, provider-owned URL, not the torrent.** The "unrestricted" link
   is time-/token-bound HTTP(S) served from the debrid CDN; the user's client never touches
   the swarm. CloudDog Fetch's analogue: after extraction+verification, the mirror URL is
   whatever the *storage* provider returns (GitHub asset URL / Drive share link / B2 URL —
   §3), recorded against {infohash, entry name, size, CRC32, SHA-256}.

**Archive handling — important honesty flag:** public debrid documentation/APIs select
*files inside a torrent*; they do **not** document extracting a single entry out of a ZIP
that is itself the torrent's sole file, nor HTTP-range extraction from archive torrents.
Claims that Real-Debrid/TorBox internally range-read ZIP central directories are
**UNVERIFIED** — no public source found in this pass (accessed 2026-10-21). The known,
implementable version of that trick is the local one in §4/§5: a ZIP is randomly accessible
*because* its index lives at the tail, so a client that can fetch arbitrary byte ranges
(here: arbitrary torrent pieces) can reconstruct selected entries without the rest of the
archive. That is a property of the ZIP format (§4), not a documented debrid feature.

**Architecture CloudDog Fetch should mirror:**

```
submit(magnet/.torrent) -> cache check (own registry: infohash+entry -> mirror URLs)
  -> miss: libtorrent selective fetch (tail, then CD, then entries)   [seedbox layer]
  -> extract + CRC/size verify per entry                              [unpack layer]
  -> store: gh release upload | rclone copy to Drive/B2/S3            [storage layer]
  -> register mirror URLs + hashes in registry; serve/register only   [unrestrict analogue]
```

---

## 3. Storage layer: rclone (+ gh CLI for GitHub)

### 3.1 rclone backend support — confirmed

- rclone is a single Go binary; its backend registry includes, among the ones we need:
  `s3` (Amazon S3 + S3-compatibles incl. Backblaze B2 via S3 API), `drive` (Google Drive,
  OAuth2), `b2` (Backblaze B2 native, application-key auth).
  — https://deepwiki.com/tgdrive/rclone/3-backend-system and
  https://deepwiki.com/wiserain/rclone/3.2-major-cloud-providers (accessed 2026-10-21);
  corroborated by rclone's own provider listing reproduced at
  https://support.lucidlink.com/hc/en-us/articles/31125359780621-Backup-cloud-storage-to-a-Filespace
  (accessed 2026-10-21), which lists Amazon S3, Backblaze B2, Google Drive, Google Cloud
  Storage, etc.
- So: **one tool covers Drive + B2 + any S3 target** with `rclone copy localfile remote:path/`.
  Use B2's native `b2` backend OR its S3-compatible endpoint via `s3` — both are documented
  rclone paths; pick native `b2` for `rclone link` support (below).

### 3.2 Mirror/share links per provider — `rclone link`

Official command doc: "`rclone link` will create, retrieve or remove a public link to the
given file or folder… If successful, the last line of the output will contain the link…
the link will always by default be created with the least constraints – e.g. no expiry, no
password protection, accessible without account." `--expire` sets expiry "**Note** not all
backends support the --expire flag"; `--unlink` removes links where supported.
— https://rclone.org/commands/rclone_link/ (accessed 2026-10-21)

Per-provider reality (secondary summary, accessed 2026-10-21:
https://rcloneview.com/support/blog/link-public-shared-links-cloud-rcloneview):

| Provider | What `rclone link` yields | Caveat |
|---|---|---|
| Google Drive | Shareable link, no expiry | **It changes the file's permission to "anyone with the link"** — a real permission mutation, not a signed URL. |
| Backblaze B2 | Download URL, no expiry | Requires a public bucket (or an auth-token URL) — private-bucket files won't be anonymously downloadable. **UNVERIFIED** against B2's own docs in this pass. |
| S3 (AWS/compat) | Pre-signed URL | Expiry-bounded (that summary says configurable, max 7 days for AWS SigV4) — a "mirror URL" from S3 is inherently temporary and must be re-mintable from the registry, not treated as permanent. |

rclone's own backend capability tables (reproduced at
https://www.systutorials.com/linux-manual-page-1-rclone/, accessed 2026-10-21) mark
LinkSharing = **Yes for Google Drive and Backblaze B2, No for Amazon S3** in that (older)
table — which conflicts with the newer pre-signed-URL behaviour described above.
**Flagged discrepancy:** treat S3 `rclone link` support as version-dependent; verify with
the installed rclone version in Phase 2 before relying on S3 links. Drive and B2 link
support is consistent across both sources.

### 3.3 GitHub Releases is NOT an rclone backend — use `gh`

No GitHub / GitHub-Releases backend appears in rclone's backend registry or provider lists
cited in §3.1 (negative finding, 2026-10-21). The correct pattern is the GitHub CLI:

- `gh release create <tag> <files>…` — "A list of asset files may be given to upload to the
  new release"; `gh release upload <tag> <files>…` adds assets to an existing release.
  — https://cli.github.com/manual/gh_release_create (accessed 2026-10-21)
- The resulting asset URL (`https://github.com/<owner>/<repo>/releases/download/<tag>/<file>`,
  via API `browser_download_url`) is the mirror URL to register.
- **Hard limit that shapes the design:** GitHub Docs — "Up to 1000 release assets may be
  associated with a single release. **Each file included in a release must be under
  2 GiB.** There is no limit on the total size of a release, nor bandwidth usage."
  — https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
  (accessed 2026-10-21). (A secondary blog claiming a 2 GB *total* per release —
  https://devopsvisions.github.io/blog/posts/github-limits/ — contradicts GitHub's own
  docs; GitHub Docs wins.) ⇒ Any extracted entry ≥ 2 GiB must be **split into <2 GiB
  parts** (with a part-manifest + whole-file SHA-256) before `gh release upload`, or routed
  to Drive/B2 instead. With ~4.6 GB across 77 entries most entries will likely fit, but
  per-entry sizes are **UNVERIFIED** until the central directory is parsed.

### 3.4 Installing rclone without sudo

- Official install page pushes the script route `curl https://rclone.org/install.sh | sudo bash`,
  and its precompiled-binary route uses `sudo cp rclone /usr/bin/` — but rclone "comes as a
  single binary file," so sudo is only about the destination directory, not the program.
  — https://rclone.org/install/ (accessed 2026-10-21)
- No-sudo pattern, documented for exactly this case ("If you don't have administrative
  privileges… download the latest binary and use it directly"): fetch
  `https://downloads.rclone.org/rclone-current-linux-amd64.zip`, unzip, `cp rclone ~/bin/`,
  add `~/bin` to PATH. — https://docs.oracle.com/en/solutions/move-data-to-cloud-storage-using-rclone/install-rclone1.html
  (accessed 2026-10-21). Same applies to `gh` (single binary tarball into `~/bin`) — the
  tarball route is standard but was not separately citation-checked in this pass:
  **UNVERIFIED** detail, low risk.

---

## 4. ZIP64 handling on a partially downloaded (sparse) file

### 4.1 Where the index lives

A ZIP is: [local header + data] × N, then the **central directory (CD)** — one 46-byte-fixed
central header per entry carrying filename, method, CRC-32, compressed/uncompressed sizes,
and the **offset of that entry's local header** — then the end records.
— https://en.wikipedia.org/wiki/ZIP_archive (accessed 2026-10-21)

- Classic EOCD (`PK\x05\x06`): 22 bytes + optional comment (up to 65,535 bytes), found by
  scanning backwards from EOF; it gives CD size + CD offset + entry count.
- **ZIP64 (mandatory here — 350 GB ≫ 4 GiB):** when sizes/offsets/counts exceed 32-bit
  limits they are set to `0xFFFFFFFF`/sentinel and the real values move to (a) ZIP64 extra
  fields (ID `0x0001`) in individual headers, in field order uncompressed size, compressed
  size, local-header offset — each present *only if* its 32-bit field is the sentinel —
  and (b) at the tail: the **ZIP64 EOCD record** (`PK\x06\x06`) immediately followed by the
  **ZIP64 EOCD locator** (`PK\x06\x07`, 20 bytes, points back to the ZIP64 EOCD), immediately
  before the classic EOCD.
  — https://deepwiki.com/zertovitch/zip-ada/8.3-zip-format-details-and-headers and
  https://deepwiki.com/danifus/pyzipper/3.4-zip64-and-large-files (accessed 2026-10-21)

Practical tail-fetch size: to bootstrap you need, at minimum, the last
`22 + 20 + 56 + 65,535` bytes worst case (EOCD + locator + ZIP64 EOCD + max comment) —
i.e. **fetch pieces covering at least the final 64 KiB + ~100 bytes; fetching the final
1 MiB's pieces is a cheap, safer bootstrap** (still one or two pieces at large P).

### 4.2 Python `zipfile` behaviour — usable, with sharp edges

- CPython docs: `zipfile` "can handle ZIP files that use the ZIP64 extensions," `ZipFile`
  requires a **seekable** file in mode `'r'`, and `allowZip64=True` is the default.
  — https://docs.python.org/pl/3.12/library/zipfile.html (Python 3.12 docs, accessed 2026-10-21;
  the `/pl/` path is the docs.python.org result returned — content is the standard library ref)
- What `zipfile` actually does on open: seeks to EOF, reads the tail window, locates the
  EOCD, follows the ZIP64 locator if present, then reads the **entire CD** into memory as
  `ZipInfo` objects. Therefore it works on our sparse file **iff**:
  1. The file's *apparent* size is the true 350 GB (create the sparse target by truncating
     to the torrent's file size — libtorrent sparse mode does this by writing pieces at
     their real offsets; holes read back as zeroes).
  2. All pieces covering [CD offset, EOF) have actually been downloaded. Holes inside the
     CD read as `0x00` and will corrupt parsing — typically `BadZipFile` or garbage
     `ZipInfo`s; zeroes are indistinguishable from real data to `zipfile`, so **gate CD
     parsing on libtorrent `have_piece()` for every CD piece**, never on "the file opened."
  3. Likewise, `ZipFile.open(entry)` / `.read(entry)` seeks to the entry's local header and
     streams `compress_size` bytes: any not-yet-downloaded piece in that span silently
     reads as zeroes and surfaces only as a **CRC-32 mismatch (`BadZipFile: Bad CRC-32`)**
     or a zlib error at the end. Gate every entry read on `have_piece()` for its full
     piece set, and still check CRC + size yourself (§5).
- What `zipfile` does **not** do: any notion of missing/partial data, HTTP-range or
  piece awareness, or per-entry "is this present?" queries. It is a parser for the CD once
  fetched — a good one. Writing a minimal hand parser for EOCD/ZIP64-EOCD/CD is also
  viable and avoids surprises; either way the *fetch* logic is ours. (Behaviour in this
  bullet is from the documented/known CPython implementation, not a single citable web
  page — treat implementation minutiae as **UNVERIFIED** until Phase 2 tests it against
  the real sparse file, which is cheap to do.)

### 4.3 Local header ≠ central header — you need one extra probe per entry

The CD gives `header_offset`, sizes, CRC and method, but the entry's *data* starts after
its **local** header, whose filename length (offset 26, 2 bytes) and extra-field length
(offset 28, 2 bytes) can differ from the CD copy: local header is 30 bytes fixed, magic
`PK\x03\x04`, method at offset 8 (0 = stored, 8 = deflate), sizes at 18/22 (or
`0xFFFFFFFF` → ZIP64 extra field follows the filename), data begins at
`header_offset + 30 + name_len + extra_len`.
— https://en.wikipedia.org/wiki/ZIP_archive (accessed 2026-10-21)
Also handle flag bit 3 (0x08): when set, local CRC/sizes are zero and a data descriptor
follows the data — irrelevant for extraction *if you trust the CD's `compress_size`/CRC*
(which you should; the CD is authoritative), but it explains zeroed local fields.
⇒ Per entry, first fetch the piece(s) covering the first ~64 KiB at `header_offset`
(name+extra are bounded by 65,535 each), parse the local header, then compute the exact
data range. In practice the local-header pieces usually coincide with / precede the data
pieces, so this is a cheap refinement pass before committing to the entry's full piece set.

### 4.4 Stored vs deflated entries — extraction + disk budget

- **Method 0 (stored):** entry bytes in the ZIP *are* the file bytes. Extraction = slice
  `[data_start, data_start + compress_size)` out of the sparse file and write it out;
  `compress_size == file_size`; verify CRC-32 of the slice.
- **Method 8 (deflated):** bytes are a raw DEFLATE stream. Decompress streaming with
  `zlib.decompressobj(wbits=-15)` in chunks (never load a whole large entry in RAM),
  counting output bytes and running CRC-32 (`binascii.crc32`) as you go; final check:
  output length == CD `file_size` **and** CRC == CD `CRC-32`. (`zipfile` will do exactly
  this for you per entry, including its own CRC check, once pieces are present.)
- Other methods (12 bzip2, 14 LZMA, 93 zstd, …) are possible in principle; which methods
  this ZIP actually uses is **UNVERIFIED** until the CD is parsed — design the extractor
  around `zipfile` (which covers 0/8/12/14) and fail loudly on anything else.
- Disk math on ~13 GB free: sparse file's *allocated* blocks ≈ wanted pieces only
  (~4.6 GB + piece-rounding overhead, §1.6) — check with `du`, not `ls` (apparent size will
  say 350 GB). Extract **one entry at a time → verify → upload → delete the extracted
  copy**; never materialise all extracted entries at once if their uncompressed total is
  materially larger than 4.6 GB (the 4.6 GB figure in the task is ambiguous between
  compressed-in-ZIP and uncompressed — **UNVERIFIED**; the entry-at-a-time loop is safe
  under either reading).

---

## 5. VERDICT

### Recommended stack

- **Engine: libtorrent 2.1.1 via its Python bindings (already installed) — the only
  candidate that passes the test.** It alone offers programmatic per-piece
  `dont_download` (priority 0) from Python, BEP 9 `ut_metadata` for magnets, BEP 5 DHT
  trackerless operation, sparse storage by default, per-piece hash verification, and
  `map_file` for authoritative range→piece mapping. aria2, qBittorrent-nox and
  Transmission all top out at per-*file* selection, which is all-or-nothing (350 GB or
  nothing) on a single-file torrent; aria2's `--bt-prioritize-piece=head,tail` prioritises
  but never excludes. Do not build on any of them for this workload.
- **Storage: rclone (single binary in `~/bin`, no sudo) for Google Drive + Backblaze B2
  (+ S3 if wanted), `gh` CLI for GitHub Releases.** `rclone link` mints mirror URLs for
  Drive/B2; treat any S3 link as an expiring pre-signed URL. GitHub has no rclone backend —
  use `gh release create/upload`, and split or reroute any entry ≥ 2 GiB (GitHub's
  per-asset limit).
- **ZIP layer: Python `zipfile` as the CD/entry parser, gated everywhere by libtorrent
  `have_piece()`, with our own EOCD/ZIP64 bootstrap fetch.** Never trust bytes from a
  sparse region until its piece is verified-present.

### Selective-download algorithm (pseudocode)

```
INPUT: magnet_uri or torrent_file, wanted_entry_selector (names/patterns -> ~77 entries),
       sparse_dir, piece budget
OUTPUT: extracted, CRC/size-verified entry files + registry of mirror URLs

# --- Phase 0: metadata -------------------------------------------------------
ses = lt.session(settings: enable DHT (BEP5), LSD/PEX on, listen on)
params = add_torrent_params(save_path=sparse_dir,
                            storage_mode=storage_mode_sparse,      # NEVER allocate
                            flags |= paused | default_dont_download)
h = ses.add_torrent(params)          # ti set if .torrent; magnet otherwise
wait for metadata_received_alert     # magnet case (BEP9); skip if .torrent supplied
ti = h.torrent_file();  P = ti.piece_length();  S = ti.file_storage().file_size(0)  # single file
N = ceil(S / P)
assert ti.num_files() == 1
sparse_path = sparse_dir / ti.file_storage().file_path(0)   # apparent size S, holes = 0

def pieces(byte_ranges):             # §1.6 mapping; cross-check with ti.map_file(0, off, len)
    out = set()
    for (start, length) in byte_ranges where length > 0:
        out |= { start//P .. (start+length-1)//P }
    return out

def fetch(piece_set, label):         # drive one selective round to completion
    prio = [0]*N
    for p in piece_set: prio[p] = 7                  # top priority; all else dont_download
    h.prioritize_pieces(prio)                        # (seed params.piece_priorities instead
                                                     #  on later resumes)
    h.resume()
    wait until all(h.have_piece(p) for p in piece_set)   # poll status / piece_finished_alerts;
                                                         # abort round on hash_failed_alert -> refetch
    h.pause()

# --- Phase 1: bootstrap the tail (EOCD + ZIP64 records) ----------------------
TAIL = 1 MiB  (generous: EOCD 22B + ZIP64 locator 20B + ZIP64 EOCD 56B + max comment 64KiB)
fetch(pieces([(S - TAIL, TAIL)]), "tail")
with open(sparse_path,'rb') as f:                    # hand-parse or zipfile._EndRecData
    locate EOCD by scanning tail window backwards for PK\x05\x06
    if ZIP64 locator (PK\x06\x07) precedes it: read ZIP64 EOCD (PK\x06\x06)
    -> cd_offset, cd_size, total_entries            # 64-bit values; do NOT trust 32-bit fields

# --- Phase 2: central directory ----------------------------------------------
fetch(pieces([(cd_offset, cd_size)]), "central-directory")   # union with tail pieces
zf = zipfile.ZipFile(sparse_path)          # now safe: every CD+tail piece is have_piece()
entries = [zi for zi in zf.infolist() if wanted_entry_selector(zi.filename)]   # expect ~77
record per entry: filename, compress_type, CRC, compress_size, file_size, header_offset

# --- Phase 3+4 per entry: local header, then data; extract-verify-store ------
registry = []
for zi in entries:                           # one at a time: disk-tight discipline (§4.4)
    # 3a. local header probe (bounded: 30B fixed + <=64KiB name + <=64KiB extra)
    fetch(pieces([(zi.header_offset, 128*1024)]) - already_have, "local-header")
    parse local header at zi.header_offset -> name_len, extra_len, method
    data_start = zi.header_offset + 30 + name_len + extra_len
    assert method == zi.compress_type        # CD is authoritative; mismatch = corrupt, stop
    # 3b. exact data range
    fetch(pieces([(data_start, zi.compress_size)]), f"data:{zi.filename}")
    # 4a. extract + verify (zipfile does method dispatch + CRC; or manual:)
    raw = read sparse_path[data_start : data_start + zi.compress_size]   # streamed, chunked
    if method == 0:  out_bytes = raw                               # stored
    elif method == 8: out_bytes = zlib.decompressobj(-15) stream of raw   # deflated
    else: FAIL LOUDLY (unsupported method)                         # bzip2/lzma via zipfile ok
    stream out_bytes -> extracted_path, while computing crc32 and byte count
    assert byte_count == zi.file_size and crc32 == zi.CRC          # ZIP-level verification
    sha256 = hash of extracted file                                # registry-level verification
    # 4b. store + register mirrors; delete local copy before next entry
    mirrors = []
    if size(extracted) < 2 GiB: gh release upload <tag> extracted  -> github asset URL
    else: split into <2 GiB parts (+ manifest) then gh upload, OR skip GitHub for this entry
    rclone copy extracted gdrive:<dest>/  ; mirrors += rclone link gdrive:<dest>/<name>
    rclone copy extracted b2:<bucket>/    ; mirrors += rclone link b2:<bucket>/<name>
    verify each store: remote size (rclone size / gh api) == local size   # + hash where offered
    registry.append({infohash, filename, file_size, crc32, sha256, mirrors})
    delete extracted_path                        # sparse file stays until ALL entries done
delete sparse torrent data; persist registry (JSON) alongside code + to a store

TOTAL downloaded = |union of all fetched pieces| * P  ≈ wanted bytes + piece rounding (§1.6)
NEVER: storage_mode_allocate, file-priority-only clients, or setting any non-wanted piece > 0.
```

### Open risks / UNVERIFIED items for Phase 2

1. This torrent's `piece_length`, total piece count, v1/v2/hybrid, tracker/DHT liveness,
   and per-entry sizes/methods — all unknown until metadata is in hand; recompute the disk
   budget from the real P first (§1.6).
2. "~4.6 GB of entries" — compressed vs uncompressed reading is unverified; the
   entry-at-a-time loop (§4.4) is safe under both.
3. Debrid-internal archive/range extraction: unverified and, for this build, irrelevant —
   CloudDog Fetch implements the range trick itself via pieces (§2).
4. S3 `rclone link` support/expiry: sources conflict (older capability table vs newer
   pre-signed-URL descriptions) — test with the installed rclone before promising S3
   mirror URLs (§3.2). B2 public-bucket requirement for links likewise to be confirmed
   against the live bucket.
5. CPython `zipfile` minutiae on sparse files (§4.2) — documented at the API level;
   test on the real file in Phase 2 (open-after-CD-fetch, CRC behaviour on holes) before
   trusting it in the pipeline.
