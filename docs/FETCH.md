# CloudDog Fetch — the self-hosted TorBox replacement

Owner directive 2026-10-20: TorBox plan-gated its API key; the owner declines
the upgrade. CloudDog Fetch is the free, open-source replacement, built into
CloudDog: it **fetches** (selective torrent download), **stores** (GitHub
Releases today; rclone targets next), and **registers mirror URLs** — the
three jobs a debrid service does, on our own stack.

Research behind the design: [RESEARCH-FETCH-2026-10-21.md](RESEARCH-FETCH-2026-10-21.md).
Headline findings:

- **No free debrid service can do the flagship job** (77 files inside one
  350 GB single-file torrent): the largest verified free cap is 10 GB, and
  every "select files" API selects among a torrent's files — this torrent
  has exactly one file. Self-hosting is not the fallback; it is the only
  free path.
- **Engine: libtorrent** (qBittorrent/Deluge's engine) via Python bindings.
  It is the only candidate with programmatic per-piece `dont_download`.
  aria2 / qBittorrent-nox / Transmission expose per-*file* selection only,
  which degenerates to all-350 GB-or-nothing here.

## How it works

```
cloud_dog.py fetch --magnet URI --zip --want-manifest WANTS.tsv \
    --repo OWNER/REPO --release TAG
```

1. **Metadata** — magnet → libtorrent (BEP 9). The torrent is added with
   `default_dont_download` + sparse storage: no piece moves until the plan
   says so, and the disk never holds more than the wanted pieces.
2. **Bootstrap** — download only the pieces covering the zip tail
   (EOCD / ZIP64 locator / ZIP64 EOCD), parse them, then the pieces
   covering the central directory. Parse the CD (ZIP64-aware): every
   entry's offset, sizes, method, CRC32.
3. **Per wanted entry** (want-manifest = `size<TAB>path` TSV, the TorBox
   lane's format; paths resolve by exact name or unique suffix):
   download only the pieces covering its local header + data range →
   extract → verify **size + CRC32 vs the CD** → SHA256 → upload to the
   release → verify against GitHub's asset digest → register the entry
   with its **mirror record** → delete the local copy (delete-as-you-go).
   A `MANIFEST-fetch-<run>.txt` asset (`sha256<TAB>size<TAB>./entry/path`,
   the gym-partner v1 format) is refreshed on the release after every
   file, so a crash loses no progress. Resume data is kept in the staging
   dir; re-running continues where the swarm left off.
4. **Mirrors** — a registry entry's record is now: hashes + size + a
   `mirrors[]` list, each `{store, repo/release/asset or remote locator,
   url, pull_back command, verified_at}`.
   - `cloud_dog.py register-mirror --file KEY --store github --repo … --release … --asset … [--verified]` — record another location for a file (dedupe-safe).
   - `cloud_dog.py pull-back --file KEY [--mirror-index N] [--out PATH]` — download it back from a mirror and verify SHA256 + size. This is the retrievability proof for every mirror.
5. **Dry run** — `--dry-run` fetches metadata + CD only and prints the
   plan (resolved wants, stage-2 bytes, ranges) without entry data.

Options: `--tracker URL` (repeatable; replaces defaults), `--add-peer
HOST:PORT` (repeatable; seedbox/internal swarms), `--limit N` (first N
wants — proof runs), `--staging DIR`, `--metadata-timeout`,
`--piece-timeout`, `--keep-local`.

Install: `pip install libtorrent` (2.x). Everything else is stdlib, like
the rest of CloudDog. rclone (single binary, no sudo:
`~/workspace/tools/rclone/rclone`) is the storage layer for Drive/B2/S3
mirror targets; configuring its remotes needs the owner's Drive OAuth /
B2 keys and is the one owner-gated follow-up. GitHub Releases is not an
rclone backend — the `gh` CLI path above is the correct one. Any entry
≥ 2 GiB must be split before GitHub upload (per-asset limit); the current
workload's largest entry is 212 MB.

## Network reality on this VM (measured 2026-10-21 — read before debugging)

- **UDP egress is blocked**: DHT, UDP trackers and uTP cannot work here.
  Sessions are configured TCP-only, uTP off.
- **Direct HTTP tracker announces are reset** by the egress guard, but
  announces **through the HTTP egress proxy succeed** — `clouddog_fetch`
  auto-configures libtorrent proxy-for-trackers from `$http_proxy`
  (peer connections stay direct). Evidence: opentrackr via proxy answered
  `complete=220 incomplete=167` for the production torrent.
- **Peer TCP from this sandbox is intercepted by the runtime egress
  policy** ("Other TCP connections is turned off"): raw BT handshakes to
  real peers are answered by the guard, and HTTP CONNECT tunnels to peer
  ports time out. Result: the production torrent's swarm is **alive and
  measured** (217–220 seeds, ~170 leechers, 50 peers returned per
  announce) but **piece transfer cannot run inside this agent sandbox**.
  The same `fetch` command on any host where peer TCP is allowed (the
  owner's other machines/lanes) runs the job unchanged:

```bash
python3 cloud_dog.py fetch \
  --magnet "$(head -1 ~/workspace/torbox-pull/documents-torrent-magnet.txt | sed 's/^magnet:/magnet:/')" \
  --zip --want-manifest ~/workspace/torbox-pull/gympartner-manifest-remaining.txt \
  --repo mhvnsnt/cn-gigaleak-my-gym-partner \
  --release gym-partner-gaps-torbox-v1
```

## Proof (2026-10-21, this repo)

End-to-end on a loopback swarm (seeder + production CLI, `--add-peer`):
a synthetic ZIP64 zip torrent (3 entries: stored 3 MB, deflated 108 KB,
one unwanted) → CD parsed → **2/2 wanted entries stored+verified** on
release `fetch-proof-20261021`, unwanted entry never stored; seeder
upload for a stage-1-only run was 117,300 B of a 3,000,884 B torrent
(selectivity evidence). Registry entries carry mirror URLs + pull-back
commands; `pull-back` verified from mirror #0 and from a second mirror
(same file on `mhvnsnt/cn-gigaleak-my-gym-partner` release
`fetch-proof-clouddog-20261021`, digest-matched before registering).
Tests: `tests/test_fetch.py` (zip planning/extraction/CRC, ZIP64 CD,
manifest, mirrors, proxy settings).
