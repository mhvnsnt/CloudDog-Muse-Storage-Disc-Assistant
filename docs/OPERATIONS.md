# CloudDog operations — `clouddog/` package

## Scope

This package EXTENDS the `cloud_dog.py` v2 engine; it does not duplicate
it. The v2 lane owns the CLI (`scan`/`register`/`verify`/`reap`/`enforce`/
`daemon`/`mirror`), the GitHub/TeraBox/Kaggle backends, daemon and mirror
modes, and the audit journal. This package adds:

- **`clouddog.policy`** — the disk laws as importable, tested code.
- **`clouddog.stores`** — the `Store` interface + the Drive backend
  (via rclone), which v2 does not ship.
- **`clouddog.config`** — validated YAML configuration.
- **`clouddog.bridge`** — registry → audit-journal bridge.

There is no `clouddog` CLI in this package: the operator-facing CLI is
`cloud_dog.py`. This package is the library the engine, the dashboard,
and guardian lanes import.

## Install

```bash
cd ~/workspace/clouddog
pip install -e ".[dev]"   # runtime + pytest + ruff
```

Python 3.10+; the only runtime dependency is `pyyaml` (stdlib otherwise).
Drive operations additionally need the `rclone` CLI on PATH.

## Config file

```python
from clouddog.config import load_config, write_default_config

write_default_config("~/.clouddog/config.yaml")  # commented default; never overwrites
cfg = load_config()  # ~/.clouddog/config.yaml, or defaults when missing
```

Reference (keys the package reads):

```yaml
stores:
  drive:
    remote: "gdrive"     # rclone remote name (required for the drive store)
    base_dir: "clouddog" # remote base dir (default)

thresholds:              # percent-used thresholds
  warn_pct: 75
  target_pct: 70
  ceiling_pct: 88

scan_roots:
  - "/home/hatch/workspace"

protected_extra: []      # extra fnmatch patterns treated as protected game files
strict_verify: false
journal_path: "~/.clouddog/journal.jsonl"
terabox_remote_root: "/CN-Gigaleak"
github_repo: ""
github_tag: ""
state_dir: "~/.clouddog"
```

Invalid YAML or mistyped keys raise `ConfigError` naming the bad key.

## Using the policy engine

```python
from clouddog.policy import laws, approvals

# Should this path be cleared? (NEVER deletes anything itself.)
d = laws.decide_delete(
    "/home/hatch/workspace/urban-reign.iso",
    verified_stores=["github"],
    owner_approved=False,
)
print(d.allowed, d.requires_owner, d.reasons)
# False True ['protected game file (...); NEVER-DELETE-GAME-FILES ...', ...]

# In-progress work is refused with no override, not even the owner's:
d = laws.decide_delete("/home/hatch/dl/part-00.part", verified_stores=["github"],
                       owner_approved=True)
assert not d.allowed

# Gate a destructive call site:
approvals.require_execute(dry_run=False, assume_yes=True, what="reap 12 files")
```

Rule order (see `docs/DESIGN.md`): in-progress → marker → protected game
file → one-store rule → nothing-verified. `laws.is_in_progress`,
`laws.is_protected_game_file`, and `laws.has_do_not_delete_marker` are
also usable individually.

## Using the Drive store

```python
from clouddog.config import load_config
from clouddog.stores import get_store

store = get_store("drive", load_config().stores)  # StoreUnavailable if rclone/remote missing
ref = store.upload("/home/hatch/big-file.bin", remote_dir="offload-2026-10-17")
assert store.verify(ref)  # size match via rclone lsjson
print(store.uri_for(ref))  # drive:gdrive:clouddog/offload-2026-10-17/big-file.bin
```

Drive is best-effort: the owner's Drive has been near-quota before, so an
upload failure raises `StoreError` and local files are never deleted on
failure. rclone remote setup stays operator-done (see the rclone docs);
the store only points at it.

## The registry → audit bridge

```python
from clouddog.bridge import import_registry_to_audit

n = import_registry_to_audit()  # defaults: the live registry + live audit journal
print(n, "registry entries folded into the audit journal")
```

Each registry entry becomes one `registry-import` audit event shaped like
v2's `audit_log` events (`ts`, `action`, sorted fields, store-specific
locators). Never raises; never modifies the registry; append-only.

## Coexistence with cloud_dog.py

`cloud_dog.py` (v2: stdlib CLI with GitHub/TeraBox/Kaggle backends,
daemon and mirror modes, audit journal) is the working engine and is wired
into the disk-space-guardian cron. This package:

- does NOT reimplement any of it (the first version of this package did —
  it was trimmed before merge per the no-duplicate-work law);
- gives the engine (and the dashboard lane) an importable policy layer,
  the missing Drive backend, validated config, and the audit bridge;
- never modifies `cloud_dog.py`, its registry, or its journal — the bridge
  only reads the registry and appends audit events.

## Tests

```bash
python3 -m pytest tests/ -q   # 25 tests: policy, config, stores ABC, drive, bridge
python3 -m pytest test_cloud_dog.py -q  # v2 engine's own suite (sibling lane)
python3 -m ruff check clouddog tests && python3 -m ruff format --check clouddog tests
```

## What this package does NOT do

- No CLI, no daemon, no mirror mode — those are `cloud_dog.py` v2.
- No GitHub/TeraBox/Kaggle backends — v2 owns them.
- No automatic deletion anywhere in this package. `decide_delete` only
  *decides*; the call site that acts on a `Decision` lives in the engine
  lane and must honor `requires_owner` and `allowed`.
- `strict_verify` is parsed but not enforced by any call site here.
