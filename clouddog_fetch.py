#!/usr/bin/env python3
"""clouddog_fetch.py — CloudDog Fetch: self-hosted, open-source fetch layer.

The TorBox replacement. TorBox gated its API key behind a paid plan upgrade
(2026-10); CloudDog Fetch pulls torrents itself with libtorrent and stores
verified files to the configured backends, registering every mirror URL in
the CloudDog registry (a file's record = hashes + every mirror location +
pull-back command).

Selective-fetch design (the 350GB rule):
    A single-file torrent whose content is one huge zip is NEVER downloaded
    whole. The engine downloads only the pieces covering:
      1. the zip tail (EOCD / ZIP64 locator / ZIP64 EOCD),
      2. the central directory,
      3. each wanted entry's local header + data range.
    Byte ranges -> piece indices -> piece priorities (everything else is
    dont_download). Extracted entries are verified against the central
    directory (size + CRC32), SHA256 is computed on land, the file is stored,
    the mirror is registered, and only then is local scratch cleared.

Engine: libtorrent (the engine under qBittorrent/Deluge) driven through its
Python bindings — a managed dependency, NOT a hand-rolled BT client.
Install: pip install libtorrent  (2.x). Everything else here is stdlib.

Network reality on the Muse VM (measured 2026-10-21): UDP egress is
blocked, so DHT / UDP trackers / uTP cannot work; raw direct HTTP tracker
announces are reset by the egress guard, BUT announces THROUGH the HTTP
egress proxy ($http_proxy) succeed (measured: 220 seeds on opentrackr for
a production hash). Sessions therefore auto-configure proxy-for-trackers
from $http_proxy when set, with TCP-only peer transport. On hosts with
open UDP, DHT can be enabled with enable_dht=True.

Zip parsing adapts the proven central-directory parser from the TorBox
lane's zip_extract_parallel.py (hero-parts-pull), generalized from HTTP
Range reads to any read_range(start, end) -> bytes source.
"""

import hashlib
import os
import struct
import time
import urllib.parse
import zlib

# ---------------------------------------------------------------- zip model

EOCD_SIG = b"\x50\x4b\x05\x06"
Z64_LOC_SIG = b"\x50\x4b\x06\x07"
Z64_EOCD_SIG = b"\x50\x4b\x06\x06"
CD_SIG = b"\x50\x4b\x01\x02"
LOCAL_SIG = b"\x50\x4b\x03\x04"
TAIL_WINDOW = 70_000  # EOCD(22) + comment(max 65535) + zip64 locator(20)


class FetchError(RuntimeError):
    pass


def find_eocd_range(total_size):
    """Byte range that must be present to locate the central directory."""
    return (max(0, total_size - TAIL_WINDOW), total_size - 1)


def parse_eocd(tail, total_size):
    """Parse EOCD (+ ZIP64 locator/EOCD via read callback) from the tail.

    tail: bytes of the range find_eocd_range(total_size).
    Returns (cd_offset, cd_size, cd_count, z64_eocd_offset|None).
    """
    base = total_size - len(tail)
    eocd_off = tail.rfind(EOCD_SIG)
    if eocd_off < 0:
        raise FetchError("EOCD signature not found in zip tail")
    eocd = tail[eocd_off:eocd_off + 22]
    if len(eocd) < 22:
        raise FetchError("truncated EOCD")
    cd_count = struct.unpack("<H", eocd[10:12])[0]
    cd_size = struct.unpack("<I", eocd[12:16])[0]
    cd_offset = struct.unpack("<I", eocd[16:20])[0]
    z64_off = None
    if cd_count == 0xFFFF or cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
        loc_rel = tail.rfind(Z64_LOC_SIG, 0, eocd_off)
        if loc_rel < 0:
            raise FetchError("zip64 locator not found before EOCD")
        z64_off = struct.unpack("<Q", tail[loc_rel + 8:loc_rel + 16])[0]
    return cd_offset, cd_size, cd_count, z64_off


def parse_z64_eocd(z64):
    """Return (cd_count, cd_size, cd_offset) from a ZIP64 EOCD record."""
    if z64[:4] != Z64_EOCD_SIG or len(z64) < 56:
        raise FetchError("bad ZIP64 EOCD record")
    return (struct.unpack("<Q", z64[32:40])[0],
            struct.unpack("<Q", z64[40:48])[0],
            struct.unpack("<Q", z64[48:56])[0])


def parse_central_directory(cd):
    """Parse central-directory bytes -> {name: entry dict}.

    entry: {name, method, comp_size, uncomp_size, crc32, local_header_offset}
    ZIP64 extra-field aware (tag 0x0001), same field order as the format:
    uncomp size, comp size, local header offset, disk number.
    """
    entries = {}
    i = 0
    n = len(cd)
    while i < n:
        if cd[i:i + 4] != CD_SIG:
            raise FetchError(f"bad CD signature at offset {i}")
        method = struct.unpack("<H", cd[i + 10:i + 12])[0]
        crc32 = struct.unpack("<I", cd[i + 16:i + 20])[0]
        comp_size = struct.unpack("<I", cd[i + 20:i + 24])[0]
        uncomp_size = struct.unpack("<I", cd[i + 24:i + 28])[0]
        fn_len = struct.unpack("<H", cd[i + 28:i + 30])[0]
        ef_len = struct.unpack("<H", cd[i + 30:i + 32])[0]
        fc_len = struct.unpack("<H", cd[i + 32:i + 34])[0]
        lho = struct.unpack("<I", cd[i + 42:i + 46])[0]
        name = cd[i + 46:i + 46 + fn_len].decode("utf-8", "replace")
        if 0xFFFFFFFF in (comp_size, uncomp_size, lho):
            ef = cd[i + 46 + fn_len:i + 46 + fn_len + ef_len]
            j = 0
            while j + 4 <= len(ef):
                tag, sz = struct.unpack("<HH", ef[j:j + 4])
                if tag == 0x0001:
                    k = j + 4
                    if uncomp_size == 0xFFFFFFFF:
                        uncomp_size = struct.unpack("<Q", ef[k:k + 8])[0]
                        k += 8
                    if comp_size == 0xFFFFFFFF:
                        comp_size = struct.unpack("<Q", ef[k:k + 8])[0]
                        k += 8
                    if lho == 0xFFFFFFFF:
                        lho = struct.unpack("<Q", ef[k:k + 8])[0]
                        k += 8
                    break
                j += 4 + sz
        entries[name] = {
            "name": name, "method": method, "comp_size": comp_size,
            "uncomp_size": uncomp_size, "crc32": crc32,
            "local_header_offset": lho,
        }
        i += 46 + fn_len + ef_len + fc_len
    return entries


def entry_data_range(local_header, entry):
    """(start, end_inclusive) of an entry's data, given its 30-byte local
    header (plus name/extra lengths read from it)."""
    if local_header[:4] != LOCAL_SIG:
        raise FetchError(f"bad local header for {entry['name'][:60]}")
    lfn = struct.unpack("<H", local_header[26:28])[0]
    lef = struct.unpack("<H", local_header[28:30])[0]
    start = entry["local_header_offset"] + 30 + lfn + lef
    return start, start + max(0, entry["comp_size"]) - 1


def local_header_range(entry):
    """Range to fetch first: local header + max plausible name/extra.

    The local extra field can differ from the CD one; fetch 30 bytes plus
    the name length from the CD record + 4096 slack, capped by comp data —
    callers read exactly 30 + lfn + lef from what they get back.
    """
    start = entry["local_header_offset"]
    return start, start + 30 + len(entry["name"].encode("utf-8")) + 4096 - 1


# ------------------------------------------------------------- range math

def merge_ranges(ranges):
    """Merge overlapping/adjacent (start, end_inclusive) ranges, sorted."""
    if not ranges:
        return []
    rs = sorted(ranges)
    merged = [list(rs[0])]
    for s, e in rs[1:]:
        if s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def ranges_to_pieces(ranges, piece_length, num_pieces):
    """Set of piece indices covering the given byte ranges."""
    pieces = set()
    for s, e in ranges:
        first = s // piece_length
        last = e // piece_length
        for p in range(first, last + 1):
            if 0 <= p < num_pieces:
                pieces.add(p)
    return pieces


def plan_zip_fetch(total_size, entries, wanted_names):
    """Ranges needed for stage 1 (locate CD) and stage 2 (entry data).

    wanted entries resolved by exact name or by suffix match (the manifest
    paths in the TorBox lane were suffixes of the zip entry names).
    Returns dict with stage1 ranges and per-entry resolved map + ranges.
    """
    by_suffix = {}
    for name in entries:
        by_suffix.setdefault(name, name)
    resolved = {}
    for want in wanted_names:
        if want in entries:
            resolved[want] = entries[want]
            continue
        hits = [n for n in entries if n.endswith("/" + want) or n == want]
        if len(hits) == 1:
            resolved[want] = entries[hits[0]]
    stage2 = []
    for want, ent in resolved.items():
        stage2.append(local_header_range(ent))
        # data range needs the local header; use an upper-bound range from
        # CD values (header size unknown until read, slack covers it: the
        # local header is inside local_header_range already; data starts
        # within that window, so request from lho to lho+hdr+comp_size).
        start = ent["local_header_offset"]
        stage2.append((start, start + 30 + len(ent["name"].encode()) +
                       4096 + ent["comp_size"] - 1))
    return {"resolved": resolved, "stage2_ranges": merge_ranges(stage2)}


def extract_entry(read_range, entry):
    """Extract one entry via read_range(start, end)->bytes.

    Verifies uncompressed size + CRC32 against the central directory.
    Returns (data_bytes, sha256_hex).
    """
    lh = read_range(entry["local_header_offset"],
                    entry["local_header_offset"] + 29)
    start, end = entry_data_range(lh, entry)
    if entry["comp_size"] == 0 and entry["uncomp_size"] == 0:
        data = b""
    else:
        raw = read_range(start, end)
        if entry["method"] == 0:
            data = raw
        elif entry["method"] == 8:
            data = zlib.decompress(raw, -15)
        else:
            raise FetchError(
                f"unsupported zip method {entry['method']} for {entry['name']}")
    if len(data) != entry["uncomp_size"]:
        raise FetchError(
            f"size mismatch for {entry['name']}: got {len(data)}, "
            f"CD says {entry['uncomp_size']}")
    crc = zlib.crc32(data) & 0xFFFFFFFF
    if crc != entry["crc32"]:
        raise FetchError(
            f"CRC32 mismatch for {entry['name']}: got {crc:08x}, "
            f"CD says {entry['crc32']:08x}")
    return data, hashlib.sha256(data).hexdigest()


# --------------------------------------------------------- manifest input

def read_want_manifest(path):
    """Read a want-manifest: TSV of <size>\\t<path> (TorBox-lane format).

    Returns [{size, path}]. Blank lines and #-comments are skipped.
    """
    wants = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit():
                wants.append({"size": int(parts[0]), "path": parts[1]})
            elif len(parts) >= 1:
                wants.append({"size": 0, "path": parts[0]})
    return wants


# ------------------------------------------------------------ mirror model

def github_asset_url(repo, release, asset):
    import urllib.parse
    return (f"https://github.com/{repo}/releases/download/"
            f"{urllib.parse.quote(release, safe='')}/"
            f"{urllib.parse.quote(asset, safe='/')}")


def make_mirror(store, repo="", release="", asset="", remote_dir="",
                dataset="", url="", b2_bucket="", b2_path=""):
    """One mirror location record for a registry entry."""
    if store == "github" and not url:
        url = github_asset_url(repo, release, asset)
    pull_back = ""
    if store == "github":
        pull_back = (f"gh release download {release} --repo {repo} "
                     f"--pattern {asset}")
    return {
        "store": store, "repo": repo, "release": release, "asset": asset,
        "remote_dir": remote_dir, "dataset": dataset,
        "b2_bucket": b2_bucket, "b2_path": b2_path,
        "url": url, "pull_back": pull_back, "verified_at": "",
    }


def add_mirror(entry, mirror):
    """Attach a mirror record to a registry entry (dedupe by store+locator).

    Returns True when added, False when an identical locator already exists.
    """
    mirrors = entry.setdefault("mirrors", [])
    key = (mirror.get("store"), mirror.get("repo"), mirror.get("release"),
           mirror.get("asset"), mirror.get("remote_dir"),
           mirror.get("dataset"), mirror.get("b2_bucket"),
           mirror.get("b2_path"), mirror.get("url"))
    for m in mirrors:
        if (m.get("store"), m.get("repo"), m.get("release"), m.get("asset"),
                m.get("remote_dir"), m.get("dataset"), m.get("b2_bucket"),
                m.get("b2_path"), m.get("url")) == key:
            return False
    mirrors.append(mirror)
    return True


# ------------------------------------------------------------ torrent core

# Public HTTP trackers used when a magnet carries none (or to supplement).
# TCP-only: this VM's UDP egress is blocked (measured 2026-10-21).
DEFAULT_TRACKERS = [
    "http://tracker.opentrackr.org:1337/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.dler.org:6969/announce",
    "http://tracker.renfei.net:8080/announce",
    "http://tracker.torrent.eu.org:451/announce",
    "http://open.acgnxtracker.com:80/announce",
]


def proxy_settings_from_env(environ=None):
    """libtorrent proxy settings for TRACKER connections from $http_proxy.

    Returns {} when no proxy is configured. Peer connections stay direct:
    on the Muse VM the egress proxy serves HTTP (tracker announces), while
    peer traffic is plain TCP. Credentials in the proxy URL are passed to
    libtorrent, never logged.
    """
    env = os.environ if environ is None else environ
    raw = (env.get("http_proxy") or env.get("HTTP_PROXY") or "").strip()
    if not raw:
        return {}
    u = urllib.parse.urlparse(raw)
    if not u.hostname or not u.port:
        return {}
    lt_proxy_pw = 5  # lt.proxy_type_t.http_pw
    lt_proxy = 4     # lt.proxy_type_t.http
    return {
        "proxy_type": lt_proxy_pw if u.username else lt_proxy,
        "proxy_hostname": u.hostname,
        "proxy_port": u.port,
        "proxy_username": urllib.parse.unquote(u.username or ""),
        "proxy_password": urllib.parse.unquote(u.password or ""),
        "proxy_tracker_connections": True,
        "proxy_peer_connections": False,
        "proxy_hostnames": True,
    }


class SelectiveTorrent:
    """libtorrent session that downloads ONLY the pieces you ask for.

    Usage:
        t = SelectiveTorrent(save_path)
        info = t.add_magnet(magnet, timeout=600)   # metadata + info dict
        t.download_pieces({1,2,3}, timeout=3600)   # sparse file on disk
        data = t.read_range(start, end)            # from downloaded pieces
    Resumable: libtorrent resume data is saved next to the payload in
    save_path/.resume.fastresume when close() is called cleanly.
    """

    def __init__(self, save_path, enable_dht=False, trackers=None,
                 listen_port=6882, use_env_proxy=True):
        import libtorrent as lt  # managed dependency; lazy import
        self.lt = lt
        self.save_path = save_path
        os.makedirs(save_path, exist_ok=True)
        settings = {
            "listen_interfaces": f"0.0.0.0:{listen_port}",
            "enable_dht": bool(enable_dht),
            "enable_lsd": False, "enable_upnp": False,
            "enable_natpmp": False,
            # UDP is blocked on the Muse VM; uTP would stall peer conns.
            "enable_outgoing_utp": False, "enable_incoming_utp": False,
            "alert_mask": lt.alert_category.error
            | lt.alert_category.status,
        }
        if use_env_proxy:
            settings.update(proxy_settings_from_env())
        self.session = lt.session(settings)
        self.trackers = list(trackers) if trackers is not None \
            else list(DEFAULT_TRACKERS)
        self.handle = None
        self.info = None

    def add_magnet(self, magnet, timeout=600, connect_peers=None):
        lt = self.lt
        params = lt.parse_magnet_uri(magnet)
        params.save_path = self.save_path
        merged = list(params.trackers) + [t for t in self.trackers
                                          if t not in params.trackers]
        params.trackers = merged
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        resume = os.path.join(self.save_path, ".resume.fastresume")
        if os.path.exists(resume):
            try:
                with open(resume, "rb") as f:
                    params = lt.read_resume_data(f.read())
                params.save_path = self.save_path
            except Exception:
                pass
        self.handle = self.session.add_torrent(params)
        for peer in connect_peers or []:
            try:
                self.handle.connect_peer(peer)
            except Exception:
                pass
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.handle.torrent_file() is not None:
                self.info = self.handle.torrent_file()
                return self.info
            if int(time.time() - t0) % 120 < 11 and time.time() - t0 > 30:
                self.handle.force_reannounce()
            time.sleep(5)
        st = self.handle.status()
        raise FetchError(
            f"metadata timeout after {timeout}s: peers={st.num_peers} "
            f"seeds={st.num_seeds} state={st.state}")

    def add_torrent_file(self, torrent_path, timeout=600):
        lt = self.lt
        ti = lt.torrent_info(torrent_path)
        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = self.save_path
        params.trackers = list(self.trackers)
        params.storage_mode = lt.storage_mode_t.storage_mode_sparse
        self.handle = self.session.add_torrent(params)
        self.info = ti
        return ti

    def status(self):
        st = self.handle.status()
        return {"peers": st.num_peers, "seeds": st.num_seeds,
                "num_complete": st.num_complete,
                "num_incomplete": st.num_incomplete,
                "progress": st.progress, "state": str(st.state)}

    def payload_path(self):
        """Path of the single-file payload (single-file torrents only)."""
        if self.info.num_files() != 1:
            raise FetchError("payload_path: multi-file torrent unsupported "
                             "for zip-range mode")
        return os.path.join(self.save_path, self.info.name())

    def download_pieces(self, pieces, timeout=7200, stall_timeout=1800):
        """Download exactly `pieces` (set of indices); block until done.

        All other pieces are set to dont_download. Raises FetchError on
        timeout/stall so callers can checkpoint and resume later.
        """
        lt = self.lt
        ti = self.info
        prios = [lt.download_priority_t.dont_download] * ti.num_pieces()
        top = lt.download_priority_t.top_priority
        for p in pieces:
            if 0 <= p < ti.num_pieces():
                prios[p] = top
        self.handle.prioritize_pieces(prios)
        self.handle.resume()
        t0 = time.time()
        last_have = -1
        last_progress_t = time.time()
        while time.time() - t0 < timeout:
            have = self.handle.status().pieces
            got = sum(1 for p in pieces if have[p])
            if got == len(pieces):
                return
            if got != last_have:
                last_have = got
                last_progress_t = time.time()
            elif time.time() - last_progress_t > stall_timeout:
                st = self.status()
                raise FetchError(
                    f"download stalled: {got}/{len(pieces)} pieces, "
                    f"peers={st['peers']} seeds={st['seeds']}")
            time.sleep(5)
        st = self.status()
        raise FetchError(
            f"download timeout after {timeout}s: peers={st['peers']} "
            f"seeds={st['seeds']}")

    def read_range(self, start, end):
        """Bytes [start, end] inclusive from the sparse payload file.

        Only valid for ranges whose pieces have been downloaded; missing
        regions read as zeros from the sparse file and callers verify via
        zip CRC/size checks.
        """
        with open(self.payload_path(), "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)

    def save_resume(self):
        try:
            self.handle.save_resume_data(
                self.lt.save_resume_flags_t.flush_disk_cache
                | self.lt.save_resume_flags_t.save_info_dict)
            t0 = time.time()
            while time.time() - t0 < 30:
                for a in self.session.pop_alerts():
                    if isinstance(a, self.lt.save_resume_data_alert):
                        with open(os.path.join(
                                self.save_path, ".resume.fastresume"),
                                "wb") as f:
                            f.write(self.lt.write_resume_data(a.params))
                        return
                time.sleep(1)
        except Exception:
            pass

    def close(self):
        self.save_resume()
        try:
            self.session.pause()
        except Exception:
            pass
