# CloudDog design

CloudDog treats local disk as a cache, not storage. Files are mirrored to
permanent stores (TeraBox, GitHub releases, Google Drive), byte-verified, and
only then eligible for clearing. The owner's standing disk laws are encoded
as code in `clouddog.policy` and every mutating action is journaled.

## Architecture

```
                        +----------------------+
                        |   clouddog CLI       |
                        |   clouddog/cli.py    |
                        | init status sweep    |
                        | store verify watch   |
                        +----------+-----------+
                                   |
                        +----------v-----------+
                        |   policy gates       |
                        |   clouddog/policy/   |
                        |   laws.* decides     |
                        |   approvals.* gates  |
                        +----------+-----------+
                                   |
              +--------------------v--------------------+
              | stores  (clouddog/stores/)               |
              | terabox | github releases | drive       |
              | upload / verify / exists + retries      |
              +--------------------+--------------------+
                                   |
                        +----------v-----------+
                        | journal (JSONL)      |
                        | clouddog/core/       |
                        | manifest.py          |
                        +----------------------+

  +------------------+        +---------------------+
  | daemon           |------->| cron (10 min)       |
  | clouddog/        |  runs  | clouddog watch      |
  | daemon.py        |  once  | --once              |
  +------------------+        +---------------------+

  +------------------+        +---------------------+
  | core             |        | config              |
  | monitor/verifier |<-------| clouddog/config.py  |
  | inventory        |  reads | YAML -> Config      |
  +------------------+        +---------------------+
```

The CLI never touches the network except through a `Store`. The daemon is a
thin loop over `run_once`; policy and journaling are shared with the CLI.

## Data flows

### store <path> --to STORE --execute --yes

1. `verifier.file_size` + `verifier.sha256_file` capture the local truth.
2. `stores.get_store(name, cfg.stores)` builds the backend (raises
   `StoreUnavailable` when the store is not configured).
3. `run_with_retries(store.upload)` performs the upload (5 attempts,
   exponential backoff).
4. `store.verify(ref)` re-checks the remote object against size+sha256.
5. On success: journal a `store` entry (`dry_run=False`) with the StoredRef.
   On failure: journal a `verify` entry (`dry_run=False`, note `FAILED`) and
   raise `VerificationFailed`. A success is never journaled without verify.

### verify --journal-entry N

1. Load entry N (1-based) from the journal.
2. Rebuild each `StoredRef` from the entry's `stores` dicts.
3. Call `store.verify(ref)` per store; print OK/FAIL.
4. Journal a `verify` entry recording the per-store results.

### sweep [--execute --yes] (default: dry-run plan)

1. `monitor.disk_usage("/")`; below `warn_pct` -> evaluation pass only:
   list candidates, take no action, journal a `sweep-plan` entry.
2. `inventory.scan` over each `scan_roots` entry, biggest first.
3. Skip candidates that are in-progress (`laws.is_in_progress`) or under a
   `.DO_NOT_DELETE` marker (`laws.has_do_not_delete_marker`).
4. Route each candidate to a store (`media-file`/`unknown` -> terabox, else
   github; `git-clone`/`venv`/`bulk-dir` -> github, else terabox).
5. Dry-run: print `PLAN` lines, journal one `sweep-plan` entry
   (`dry_run=True`), exit 0.
6. `--execute --yes`: `approvals.require_execute` gates it (exit 2 without
   `--yes`); each file candidate is uploaded + verified + journaled as a
   `store` entry (`dry_run=False`). Directories are skipped in v1.

### watch [--once] / daemon

`daemon.run_once(config)`:

1. Snapshot `disk_usage` per scan root.
2. Collect journal entries with `action == "store"` and `dry_run == False`
   as the already-mirrored set.
3. Scan roots, take the top 5 candidates by size; skip in-progress, marked,
   already-mirrored, and non-files (v1 mirrors files only).
4. Upload each to the first configured store with retries; verify; journal
   `store` on success or `verify`/`FAILED` on failure. Any `StoreError`
   becomes a note, never a crash.
5. Return `{ts, disk, candidates, mirrored, journal_entries, notes}`.

`run_loop` prints one line per run and sleeps `interval_sec` unless `once`.

## Safety invariants

- **Dry-run default.** Every mutating command plans first; `--execute`
  plus `--yes` is required to act. Missing `--yes` exits 2 with the exact
  flag spelled out.
- **In-progress is sacred.** Anything downloading, uploading, rendering,
  or extracting (suffixes like `.part`, or staging/download/tmp dirs) is
  never touched -- no override exists, not even the owner's.
- **Protected game files need the owner.** ISOs, ROMs, game archives, and
  anything under `teardown/`, `game-reverse-engineering/`, `chimera-roms/`
  (plus `protected_extra` patterns) require owner approval AND a verified
  store before any delete; v1 never deletes them at all.
- **No delete without a verified store.** The ONE-STORE RULE: a file may be
  cleared only after it verifies 100% (hash + size) on GitHub OR TeraBox.
  v1 implements the mirror half; deletion is a future explicit `reap`.
- **Journal everything.** Every store, verify, and sweep plan appends a
  JSONL entry (`~/.clouddog/journal.jsonl` by default). Failed verifies are
  journaled as failures, never as successes.
- **Bounded daemon.** Top 5 candidates per run, no deletes, failures become
  notes. Safe to run every 10 minutes from cron.
