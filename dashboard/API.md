# CloudDog Dashboard API contract (v1)

The dashboard backend (`clouddog/dashboard/server.py`, stdlib `http.server`
only) implements exactly these JSON endpoints. The frontend
(`clouddog/dashboard/static/`) calls only these. **No endpoint may return
hardcoded or mock numbers** — every value is computed live from the system,
the engine, or real tool output.

Base URL: `http://127.0.0.1:8899` (default; `--port` overrides). Bind to
127.0.0.1 only. All responses are JSON. Errors: `{"ok": false, "error": "..."}`.

Security rules (all enforced server-side):
- Any `path` parameter: resolve realpath and require it under `/home/hatch`
  (or the configured scan root). Reject traversal.
- `POST /api/cli`: argv allowlist — first element must be a real clouddog
  subcommand; no shell; each arg passed as a separate argv element.
- Never include secrets, tokens, or cookie values in any response.

## Endpoints

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | /api/health | — | {ok, version, time_utc} |
| GET | /api/disk | — | {path, total_bytes, used_bytes, free_bytes, use_pct} from `shutil.disk_usage` |
| GET | /api/du | ?path=&depth=1 | {path, entries:[{path, bytes, mtime}]} — real `du` walk, capped at 200 entries, sorted desc |
| GET | /api/backends | — | [{name, health: live\|degraded\|down, reason}] — real `health()` from each backend |
| GET | /api/sysinfo | — | {cpu: {load_avg_1min, load_avg_5min, load_avg_15min, cpu_count}, mem:{total_bytes, used_bytes, pct}, disk:{...same as /api/disk}, gpu: null or {name, ...} if nvidia-smi exists} |
| GET | /api/policy/scan | ?path= | [{path, bytes, action, tier, reason}] — real `policy.evaluate` over top-level entries |
| GET | /api/lanes | — | [{lane, manifest_path, uploaded, total, pct, alive, log_tail:[last 10 lines]}] — read from real lane manifests (`blob_checkpoint.json` files under ~/workspace) + `ps` aliveness. Also appends a `cloud-dog:registry` lane read live from `~/workspace/agent-ops/cloud-dog-registry.json` (accepts both the dict `{"entries": {...}}` contract and the bare-list shape active cleanup lanes write; counts/sizes come straight from the file, never a zero sentinel). |
| POST | /api/store | {path, tier, remote_ref, dry_run} | {ok, stored:{...} or dry_run preview} |
| POST | /api/verify | {path, stored} | {ok, match, detail} |
| POST | /api/reap | {manifest, src_dir, dry_run=true, backend, remote_ref} | {ok, reaped, freed_bytes, mismatches, halted} — one reaper pass. Live reap requires backend+remote_ref (verified against the LIVE store); dry_run defaults true. |
| POST | /api/sweep | {dry_run} | {ok, job_id, status:"running"} — 202 immediately; the tick runs in background. Poll below. |
| GET | /api/sweep/status | ?job=<id> | {ok, job_id, status: running\|done\|error, report?, error?} |
| GET | /api/audit | ?limit=100 | [{ts, kind, path, size, locator, note}] — real audit journal |
| POST | /api/daemon/install | {os: linux\|macos\|windows, interval_minutes} | {ok, os, interval_minutes, files:[{filename, content}]} — REAL generated config: systemd timer+service (linux), LaunchAgent plist (macos), Task Scheduler PS1 (windows), running `clouddog daemon --interval N` |
| GET | /api/repos | ?owner=mhvnsnt | [{name, description}] via `gh api` |
| GET | /api/repos/{o}/{r}/contents | ?path= | [{name, type, size, path}] via `gh api` |
| POST | /api/cli | {argv:[...]} | {ok, stdout, stderr, returncode} — allowlisted clouddog subcommands only |
| POST | /api/broadcast/draft | {title, laws:[{name, text}]} | {ok, markdown} — formatted proposal for owner approval. NEVER writes AGENTS.md/MEMORY.md. |
| POST | /api/relocate | {path, tier, remote_ref, dry_run=true} | {ok, stored, shim} — moves a dir to a tier after policy gate + per-file hash verify; writes the retrieval shim BEFORE clearing; dry_run previews. Terabox refused (no remote checksum). |
| GET | /api/torrent/status | — | {available, reason, version, torrents:[{id, name, progress_pct, down_rate, state}]} from `clouddog.torrent` |
| POST | /api/torrent/add | {magnet, tier, remote_ref} | {ok, id} or {ok:false, error} — streams pieces straight to the store tier with immediate piece deletion when libtorrent is available |

## Frontend panels (vanilla JS, no build step)

Mirror the owner's panel concepts, all live: Header (real disk meter),
StorageOverview (real df), ClutterTable (real /api/policy/scan),
TerminalPanel (POST /api/cli), DaemonScheduler (POST /api/daemon/install +
show generated config), RepoExplorer (GET /api/repos...), TorrentMirror
(/api/torrent/*, shows real availability), ComputePanel (/api/sysinfo —
no fake vVRAM, no fake GPU), EmergencyReaper (/api/lanes + POST /api/reap
+ POST /api/sweep), BroadcastPanel (/api/broadcast/draft),
RelocationWizard (/api/relocate).
