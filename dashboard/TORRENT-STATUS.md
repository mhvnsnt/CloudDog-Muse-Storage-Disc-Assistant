# Torrent Mirror Engine — Status

**Verdict: LIVE** (real libtorrent ingest, not a stub) — as of 2026-10-17.

## What was tried (environment probe)

1. `python3 -c "import libtorrent"` → `ModuleNotFoundError: No module named 'libtorrent'`
   (expected — bindings were not pre-installed).
2. `pip install --user libtorrent` → **blocked by PEP 668**
   (`error: externally-managed-environment` on this Debian-based Python 3.12).
3. `pip install --user --break-system-packages libtorrent` → **SUCCESS**:
   `libtorrent-2.1.1-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
   (8.2 MB) installed, `import libtorrent` → version `2.1.1.0`.
4. `apt list --installed | grep -i torrent` → nothing (informational only;
   no apt install attempted).

Verified live with synthetic torrents built from local files: session start,
`add_torrent`, `file_progress()`, `file_priority()`, `torrent_file()`,
`add_magnet_uri`, and the full pump cycle (upload → verify → delete local)
all exercised in `tests/test_torrent.py` — 11/11 pass, no network downloads.

## What's needed at deploy time

- `pip install libtorrent` on the machine that runs the pump. Manylinux
  wheels exist for common CPython versions (tested: cp312 x86_64). On PEP-668
  systems (Debian/Ubuntu) this needs a venv or
  `pip install --user --break-system-packages libtorrent`.
- If libtorrent is ever missing, the module degrades honestly instead of
  faking it: `libtorrent_status()` → `{"available": False, "reason": <exact
  blocker>, "version": None}`, `add_magnet()` raises `CloudDogError`, and
  `torrent_status()` returns `[]`. The dashboard should surface that status.

## Design (delete-as-you-go for torrents)

- Pieces land in `~/.clouddog/torrent-pieces/<torrent-id>/`; state lives in
  `~/.clouddog/torrents.json` (magnet, tier, remote_ref, uploaded files with
  their `StoredRef` receipts). `CLOUDDOG_HOME` overrides the home dir (tests).
- **File-granular, not piece-granular** (documented choice in the module
  docstring): a background pump thread uploads each *completed file* via
  `backend.store()` — which byte-verifies before returning per the
  `StoreBackend` contract — then deletes the local copy immediately.
  Uploaded files get libtorrent file-priority 0 so they are never
  re-downloaded, and are recorded in the state file so restarts resume
  without re-uploading. Piece-granular reassembly was rejected as fragile
  against libtorrent's storage layer for zero benefit: the local piece dir
  never holds more than the in-flight remainder.
- Backends: pass a `StoreBackend`-like object directly, or a tier name
  (`github` / `terabox` / `drive`) resolved lazily — missing tier modules
  raise an honest `CloudDogError` instead of pretending.
- `remove_torrent(tid, delete_pieces=True)` detaches from the session and
  wipes the piece dir; unknown ids raise `CloudDogError`.

## Known limitations (v1)

- libtorrent 2.1.x python bindings flag `ti.files()`/`orig_files()`/
  `file_at()` as deprecated with no warning-free replacement exposed yet;
  the module isolates that enumeration in one helper with the warnings
  suppressed and a comment (the APIs are stable and correct). Production
  paths otherwise use the current API (`torrent_file()`,
  `add_torrent_params`, `file_progress()`).
- The pump uploads whole completed files, so peak local disk use per
  torrent ≈ size of the largest in-flight file, not the whole torrent —
  by design, per DELETE-AS-YOU-GO.
- Magnet metadata fetch needs working DHT/trackers at runtime; not
  exercised in tests (no network in tests, by rule).
