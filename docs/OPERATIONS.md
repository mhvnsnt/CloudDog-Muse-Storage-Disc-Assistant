# CloudDog operations

## Install

```bash
cd ~/workspace/clouddog
pip install -e ".[dev]"   # runtime + pytest + ruff
```

This installs the `clouddog` console script (`clouddog.cli:main`).
Python 3.11+; runtime deps are stdlib + `requests` + `pyyaml`.

## Config file

`clouddog init` writes a commented default to `~/.clouddog/config.yaml`
(`--config PATH` overrides; `--force` overwrites). Reference:

```yaml
# Permanent stores to mirror to. Keys are the store names used by the CLI.
stores:
  terabox:
    creds_path: "~/workspace/tools/terabox-uploader/.creds.json"  # default
    remote_root: "/CN-Gigaleak"                                    # default
  github:
    repo: "mhvnsnt/drive-storage"   # required for the github store
    tag: "bulk-archive-2026-10-17"  # required for the github store
  drive:
    remote: "gdrive"                # rclone remote name; requires rclone on PATH

# Disk usage thresholds (percent used).
thresholds:
  warn_pct: 75      # sweep acts at/above this; below it, evaluation pass only
  target_pct: 70
  ceiling_pct: 88

scan_roots:           # roots scanned for sweep candidates
  - "/home/hatch/workspace"

protected_extra: []   # extra fnmatch patterns treated as protected game files
strict_verify: false  # reserved; one-store rule applies when false
journal_path: "~/.clouddog/journal.jsonl"
terabox_remote_root: "/CN-Gigaleak"  # default remote dir for terabox uploads
github_repo: ""
github_tag: ""
state_dir: "~/.clouddog"
```

## CLI commands

Dry-run is the default everywhere. Nothing uploads without `--execute`,
and `--execute` also needs `--yes` (missing `--yes` exits 2 and prints the
exact flag to re-run with).

```bash
clouddog init [--config PATH] [--force]
# Write the default config. Never overwrites without --force.

clouddog status [--config PATH]
# Disk usage for / and each scan root, journal entry count + last 3 entries
# rendered as markdown, and per-store "configured" (no network calls).

clouddog sweep [--config PATH] [--dry-run] [--execute] [--yes] [--owner-approved]
# Default: print what WOULD be mirrored and journal a sweep-plan entry.
# Below warn_pct: evaluation pass only, no actions, even with --execute.
# --execute --yes: mirror file candidates (upload + verify + journal).
# NEVER deletes. Directories are plan-only in v1. In-progress candidates
# and .DO_NOT_DELETE-marked trees are skipped with a reason printed.

clouddog store <path> --to STORE [--remote-dir D] [--remote-name N] \
    [--execute] [--yes] [--config PATH]
# Default: print the plan (target store, remote path, size, sha256).
# --execute --yes: upload with retries, verify, journal a store entry.
# On verify failure: journals a verify entry (note FAILED) and raises.

clouddog verify --journal-entry N [--config PATH]
# Re-verify every store listed on journal entry N (1-based), print OK/FAIL
# per store, and journal a verify entry with the results.

clouddog watch [--config PATH] [--interval SEC] [--once]
# Run the daemon mirror pass. --once (default for cron) runs a single pass
# and prints a one-line report; otherwise it loops every SEC seconds.
```

Exit codes: 0 ok, 1 error (bad config, unknown store, failed verify,
out-of-range journal entry), 2 approval required (re-run with `--yes`).

## Cron wiring (example)

A 10-minute cron keeps disk mirroring continuously without operator input:

```cron
*/10 * * * * /home/hatch/.local/bin/clouddog watch --once >> ~/.clouddog/watch.log 2>&1
```

Each run prints one line: `[ts] candidates=N mirrored=M journal_entries=K
notes=J`. The daemon never deletes, caps itself at 5 candidates per run,
and turns every store failure into a note instead of crashing.

Creating, enabling, or changing the real cron entry is the OPERATOR's job,
not CloudDog's. CloudDog only provides `watch --once` as the thing to call.

## TeraBox credentials

One-time operator setup follows the documented session-keeper flow (browser
sign-in, then `setup_creds.py` writes `.creds.json`). CloudDog reads
`~/workspace/tools/terabox-uploader/.creds.json` at runtime (override with
the store's `creds_path` key) and never handles raw cookie values — never
paste tokens into the config, never commit `.creds.json` (it is chmod 600
and gitignored). If the creds file is missing or stale, the terabox store
raises `StoreUnavailable` with a clear message and the daemon notes it and
moves on.

## Coexistence with cloud_dog.py

`origin/main` already carries PR #1's `cloud_dog.py` (stdlib-only,
verify-then-delete CLI: `scan` / `register` / `verify` / `reap` / `enforce`)
and PR #2's spark-app files. `cloud_dog.py` is wired into the
disk-space-guardian cron and keeps its registry at
`~/workspace/agent-ops/cloud-dog-registry.json`.

The `clouddog/` package EXTENDS it rather than replacing it:

- `cloud_dog.py` is the working verify-then-delete engine for GitHub
  releases. It names TeraBox but cannot live-verify it.
- `clouddog/` adds: the TeraBox backend (the 5TB primary bulk store,
  implementing the in-house tbupload.py protocol in-process), the Google
  Drive backend, the policy-as-code law engine
  (`clouddog/policy/laws.py`), structured YAML config, daemon mode
  (`clouddog watch`), and the append-only audit journal.

The bridge is `Journal.import_registry()` (`clouddog/core/manifest.py`): it
imports `cloud_dog.py` registry entries into the journal so there is ONE
audit trail across both tools. It never modifies the registry file.

```bash
python3 -c "
from clouddog.core.manifest import Journal
n = Journal('~/.clouddog/journal.jsonl').import_registry(
    '~/workspace/agent-ops/cloud-dog-registry.json')
print(n, 'entries imported')
"
```

Never modify `cloud_dog.py` itself — it is the other lane's working engine.

## Journal

Default location: `~/.clouddog/journal.jsonl` (see `journal_path`). One
JSON object per line, fsync'd on every append. Actions: `store`, `verify`,
`sweep-plan` (v1; a future `reap` will add `delete`). `clouddog status`
shows the count and the last 3 entries.

## Recovery

Re-verify anything from the journal at any time:

```bash
clouddog verify --journal-entry 12
```

This re-checks the remote objects named on entry 12 against their recorded
size+sha256 and journals the outcome. If a store lost an object, the entry
tells you exactly which remote path to re-upload with `clouddog store`.

## What v1 does NOT do yet

- No automatic deletion. `sweep --execute` mirrors only; clearing verified
  files is a future explicit `reap` command gated by `laws.decide_delete`
  (owner approval for protected game files, verified-store requirement).
- Directory candidates (git clones, venvs, bulk dirs) are plan-only; v1
  mirrors files.
- No cloud-to-cloud transfer lane yet (GitHub release -> TeraBox direct).
- `strict_verify` (require all named stores instead of the one-store rule)
  is parsed but not enforced.
