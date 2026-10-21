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
