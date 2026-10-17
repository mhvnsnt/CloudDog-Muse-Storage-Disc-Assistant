# CloudDog design — `clouddog/` package

## Scope

This package EXTENDS the `cloud_dog.py` engine; it does not duplicate it.
The v2 lane owns the GitHub/TeraBox/Kaggle backends, the CLI surface
(`scan`/`register`/`verify`/`reap`/`enforce`/`daemon`/`mirror`), daemon
mode, mirror mode, and the audit journal (`cloud-dog-audit.jsonl`).

What this package adds — the pieces v2 does not have:

- **`clouddog.policy`** — the owner's disk laws as importable, tested code:
  protected game-file matching, in-progress transfer detection, the
  ONE-STORE decision engine, and approval gates. v2's engine enforces
  inline checks; this is the formal policy layer any lane (engine,
  dashboard, guardian) can call.
- **`clouddog.stores`** — the importable `Store` interface (`upload`,
  `verify`, `exists`, `uri_for`, retries, secret redaction) plus the
  backends v2 does not ship — currently **Drive via rclone**.
- **`clouddog.config`** — validated YAML configuration (`Config`
  dataclass, `ConfigError` with named keys, `write_default_config`).
- **`clouddog.bridge`** — folds `cloud_dog.py`'s registry
  (`cloud-dog-registry.json`) into the v2 audit journal as
  `registry-import` events, so there is one audit trail.

## Architecture

```
  +---------------------+        +----------------------+
  | cloud_dog.py (v2)   |        | clouddog/ package    |
  | CLI: scan/register  |        |                      |
  | verify/reap/enforce |        | policy/  laws as     |
  | daemon/mirror       |        |   code: decide_      |
  | backends: github /  |        |   delete, approvals  |
  |   terabox / kaggle  |        |                      |
  | audit journal       |<-------| stores/  Store ABC   |
  | (cloud-dog-audit    | bridge |   + drive (rclone)   |
  |  .jsonl)            |        |                      |
  +---------------------+        | config   YAML        |
                                 | bridge   registry -> |
                                 |   audit events      |
                                 +----------------------+
```

The package never shells out to `cloud_dog.py` and never modifies its
files; the bridge only *reads* the registry and *appends* audit events.

## The policy engine (`clouddog.policy.laws`)

`decide_delete(path, *, verified_stores, owner_approved=False,
extra_patterns=()) -> Decision(allowed, requires_owner, reasons)` applies
the owner's laws in a fixed rule order:

1. **In-progress → never.** Anything downloading, uploading, rendering, or
   extracting (transient suffixes like `.part`/`.aria2`, staging/download/
   tmp dirs) is refused with `allowed=False` and `requires_owner=False` —
   the law provides no override, not even the owner's.
2. **`.DO_NOT_DELETE` marker → blocked.** Any marker file in the path's
   ancestry vetoes the whole tree (`requires_owner=True`; only the owner
   resolves it by removing the marker).
3. **Protected game files → owner + verified store.** ISOs, ROMs, disc
   images, and archives under `teardown/`, `game-reverse-engineering/`,
   `chimera-roms/`, `isos/`, `roms/` (plus `protected_extra` config
   patterns) need `owner_approved=True` AND at least one verified store
   (NEVER-DELETE-GAME-FILES + ONE-STORE RULE).
4. **Ordinary file + ≥1 verified store → allowed** (ONE-STORE RULE:
   verified 100% on at least one store).
5. **Nothing verified → refused** (NO-DELETE-BEFORE-SAFE-STORAGE).

`approvals.require_execute(dry_run, assume_yes, what)` is the last gate
before any destructive call: silent on dry-run, raises `ApprovalRequired`
without an explicit `--yes`.

## The Drive store (`clouddog.stores.drive.DriveStore`)

v2's engine covers GitHub, TeraBox, and Kaggle; bulk media also needs
Google Drive (rclone `drive` backend, Tier 1). The store shells out to the
`rclone` CLI (no new Python dependencies):

- `upload`: `rclone copyto <local> <remote>:<base>/<dir>/<name>`
- `verify`/`exists`: `rclone lsjson` size match
- Missing rclone or unconfigured remote → `StoreUnavailable` with the
  operator setup pointer (rclone remote setup stays operator-done)
- Upload failure never deletes local — Drive is best-effort (the owner's
  Drive has been near-quota before)

Retries use `stores.run_with_retries` (exponential backoff); every error
message passes through `stores._redact` so no secret material leaks.

## The bridge (`clouddog.bridge`)

`import_registry_to_audit(registry_path=..., audit_path=...) -> int`:

1. Reads `cloud-dog-registry.json` (`{"version": 1, "entries": {...}}`).
2. Converts each entry to a v2-shaped audit event (`action:
   "registry-import"`, UTC `ts`, store-specific locators per the registry
   contract, `imported_by: "clouddog.bridge"`).
3. Appends the events to `cloud-dog-audit.jsonl`.

Never raises (missing/corrupt registry → 0; failed writes skipped) and
never modifies the registry. This is how registry history joins the
single audit trail.

## Safety invariants

- **Extend, never duplicate.** If the v2 engine owns an area (backends,
  CLI, daemon, journal), this package does not reimplement it.
- **In-progress is sacred.** No override exists — not even the owner's.
- **Protected game files need the owner.** ISOs/ROMs/teardown trees
  require explicit owner approval AND a verified store.
- **No delete without a verified store.** ONE-STORE RULE, encoded in
  `decide_delete`, tested.
- **Secrets never leak.** `_redact` scrubs token/cookie-shaped values from
  every store error; the TeraBox credential file is only ever read at
  runtime by the v2 engine, never logged.
- **Append-only audit.** The bridge only appends; it never edits history.
