# CloudDog Thresholds — Research Consensus

**Status: PROPOSED — not yet approved.** Current code values stay exactly as-is
until the owner approves a change. This document records the team's multi-angle
research (owner order 2026-10-17 ~20:48 CDT: "the whole agent team... what they
all agree on... best research... different directions... don't be lazy").

## The four thresholds (current code values)

| Threshold | Value | Where | What it does |
|---|---|---|---|
| `TEN_MB` | 10 MB | `src/clouddog/policy.py` | Files ≥10MB → `STORE_AND_CLEAR` (mirror, clear local only after byte-verified store); smaller files are mirrored but kept local |
| `FIVE_HUNDRED_MB` | 500 MB | `src/clouddog/policy.py` `suggest_tier` | Files ≥500MB (or media extensions) → TeraBox tier; else GitHub |
| `ONE_HOUR` | 1 hour | `src/clouddog/policy.py` | Files <10MB modified within the hour → `KEEP_LOCAL` (hot; mirroring delayed, never skipped) |
| `FIVE_MINUTES` | 5 min | `src/clouddog/policy.py` `_is_sacred`, `config.json` `inflightMtimeSecs` | File >1MB modified <5min ago → `SACRED` (do not touch; in-flight backstop) |

## Angle A — his real disk (full census, 318,314 files, 88.65GB)

- **10MB: KEEP.** 879 files ≥10MB hold 74.7% of bytes; median file is ~35KB (285× below the floor). Dropping to 1MB would 5× the per-file ops for only +17% bytes. Cleanest empirical cut in the distribution.
- **500MB: KEEP, with nuance.** Only 12 real files ever reach it; the real bulk is the 100–500MB band (176 files / 28.4GB) which routes to GitHub — watch that band, not the giants. (Git packs dominate the ≥500MB count but never reach the tier decision — excluded as tooling.)
- **1hr: KEEP.** Holds ~10.6GB / ~90k files local during bursts; 30min vs 1hr differ by only ~3k files empirically. Cheap insurance.
- **5min: KEEP (10min harmless alternative).** Active writes settle in minutes; stale partials are permanently protected by the suffix rule (freshest `.part` found was 31 hrs old), which the data shows is the rule doing the real work.
- Caveat: scans ran during a heavy agent burst (~48k files/min); hot-window byte figures are burst-inflated — re-measure on a quiet day before hardening into law.

## Angle B — industry practice (rclone, restic, Kopia, bup, git-annex, Syncthing, Time Machine, S3/Azure)

- **10MB: KEEP.** AWS CLI treats a file as "large" at 8MB (`multipart_threshold`); per-object overhead dominates below ~MB scale. 10MB sits at the industry "worth the bookkeeping" boundary with margin.
- **500MB: KEEP.** Between rclone's 200MiB big-upload line and GitHub's 2GB/asset hard cap, with 4× headroom; a stricter variant of git-annex's canonical 100MB stay-local/archive split.
- **1hr: KEEP.** Three independent convergences on exactly one hour: Apple Time Machine's hourly cadence, rclone's `--vfs-cache-max-age` 1h, Syncthing's 3600s default rescan.
- **5min: KEEP.** Exact match: rclone's `--dir-cache-time` 5m0s. Correct order of magnitude for a polling sweeper — between seconds-scale settle debounces (rclone write-back 5s, chokidar 2s) and the 1h hot window.
- Meta-pattern: industry thresholds anchor on protocol hard limits, cost break-even points, or round human-scale values — ours are the same kinds of anchors, now documented instead of guessed.

## Angle C — his pipeline (log evidence)

- **10MB: KEEP — it's a measured boundary, not a guess.** `disk-cleanup-log.md:3239`: GitHub blob API 422'd on a 56.9MB file; max proven success 9.4MB; guardian patch set `BLOB_API_MAX = 10MB` (`trippedd-copylane/push_sg.py:15-18`). The 10MB line maps exactly onto his two upload paths (≤10MB blob API, >10MB release assets / local-clone push).
- **500MB: MOOT while TeraBox is down; operational chunk = 100MB via curl.** Log evidence: 1.9GB part → proxy refused; 500MB via `gh` → SIGTERM/timeout/404; 485MB gumball segments → 3 failed attempts. What works: 100MB chunks via curl direct to `uploads.github.com` — first-attempt `http=201` + hash-verified delete-as-you-go across three lanes (`_gamefile-stage/upload-curl.log`, `blob-reaper.log` 53 checked / 5.5GB freed, `scooby-mystery-coast/transfer2.log` 79 parts). The 200–400MB band is untested — stay at 100MB. `gh release upload` should be treated as deprecated for large files through this proxy. With no TeraBox creds, every file routes to GitHub releases anyway.
- **1hr: KEEP.** Its only effect is delaying (never skipping) mirroring of sub-10MB files that are never auto-cleared — conservative costs nothing.
- **5min: KEEP as backstop only.** Adequate for streaming downloads and sub-4-min chunk uploads, but the 25-min defjam stall proves write/read gaps exceed 5 min — it must stay behind pid/throughput/suffix/registry signals, never stand alone. The scooby reaper's fail-closed halt is the model.

## Consensus recommendation

**Keep all four thresholds in code. Change nothing until the owner approves.**

| Threshold | Recommendation | One-line rationale |
|---|---|---|
| 10MB store_and_clear floor | Keep | Equals the measured GitHub blob-API 422 point — a real operational boundary |
| 500MB terabox tier | Keep in code; **moot while TeraBox is down** | Safe under GitHub's 2GB cap; operational chunk size is 100MB via curl (proven), not 500MB |
| 1-hour hot window | Keep | Only delays mirroring of never-cleared small files; conservative costs nothing |
| 5-min sacred mtime | Keep as backstop | Real in-flight cases are covered by suffixes/registry/lane manifests; mtime stays behind them |

## Follow-ups (for the owner to approve)

1. Write the one-line rationale for each threshold into the CloudDog config/docs so nobody re-litigates them.
2. Re-measure the hot-window byte figures on a quiet (non-burst) day before hardening into law.
3. When TeraBox API returns, revisit whether the 500MB tier boundary still earns its place or should move.
4. Treat `gh release upload` as deprecated for large files; standardize the curl path (`upload-curl.sh` pattern).
