# Training-Loop SOP — CloudDog is fed for correctness, starved for violations

Owner law (2026-10-20): CloudDog's old feedback rewarded **deletion**
(bytes freed). That trained the behavior that deleted unregistered
work. The training layer replaces it:

- **Fed** = correct behavior grows the clean-cycle streak toward
  earned autonomy (`APPLY`).
- **Starved** = a `VIOLATION` or `NEAR_MISS` revokes autonomy to
  `REVOKED` on the spot, persisted immediately.
- **Bytes freed is a footnote**, printed last on every surface.

Code: `src/clouddog/scorecard.py` (grading + guardian adapter +
mirror-coverage detector), `src/clouddog/trust.py` (earned autonomy),
wired into the daemon, reaper, streamer, the v1 CLI (`clouddog
scorecard`), and the root guardian script (`cloud_dog.py scorecard`).

## Grades

| Grade | Meaning | Trust effect |
|---|---|---|
| `CORRECT` | Stored + live-verified + lawfully cleared through the delete gate, or correctly stored-and-kept / correctly kept-local | feeds the streak |
| `BLOCKED` | A gate/policy stopped a bad action with margin (multiple failing legs, trust refusal, policy refusal) — good behavior, counted as such | feeds the streak |
| `NEAR_MISS` | A delete intent reached the delete gate and was stopped by **exactly one** failing leg — a violation but for that gate | **revokes to REVOKED immediately** |
| `VIOLATION` | A delete executed without full gate proof (must stay impossible post-PR #45; graded if evidence ever appears) | **revokes to REVOKED immediately** |
| `UNMIRRORED_OLD` | Mirror-coverage **failure state**: work/scratch/intermediate files older than the mirror age (default 6h) with no registry entry and no store record | cycle is **not clean** (streak resets); surfaced at the TOP of the scorecard/heartbeat |
| `ERROR` | Action attempted, failed without deleting (backend down, store/verify crashed; file kept) | neither fed nor starved; reported per actor |

**Attribution is step 1** (governance research, PR #47): every graded
record carries `actor`, `invocation_id`, the grade, and the gate-leg
results (`blocks`). The pre-training journal had no actor field —
that is why a disappearance could stay anonymous. Grades are reported
**per actor**, never collapsed to a single number.

**Grade the trace, not just the outcome**: a clear executed on a
**stale verify** (byte-proof from an earlier run — e.g. streamer
chunks resumed from a checkpoint) grades `NEAR_MISS`, not `CORRECT`,
even though the bytes survived on the store.

**No proxy metrics**: `freed_bytes` is telemetry only — never a
grade, never a promotion input, never a success condition. The streak
logic reads grades and findings only. (The original flaw was reward
hacking/Goodhart: optimizing the byte proxy trained the deleter.)

Guardian actions (root `cloud_dog.py enforce/reap`, which never pass
through `delete_gate`) are graded from the root audit log
(`~/workspace/agent-ops/cloud-dog-audit.jsonl`) through the adapter
mapping in `scorecard.py` / `cloud_dog.py` (`reap` → CORRECT clear,
`reap_policy_block` / `reap_keep` / verify-fail → BLOCKED, skips →
CORRECT kept, …) and merged into the same card.

## Trust lifecycle

State file: `<state dir>/trust.json`, where the state dir is
`$CLOUDEDOG_STATE_DIR` or `~/.clouddog` — **never a repo checkout**
(state keyed to a checkout forks the truth; a dormant second checkout
exists on the box).

```
REVOKED ──first clean cycle──▶ PROBATION ──required_streak (default 20)
consecutive clean cycles──▶ APPLY
   ▲                                │
   └──── VIOLATION / NEAR_MISS anywhere ────┘  (revokes on the spot)
```

- First run initializes **REVOKED** with reason `owner revocation
  2026-10-20 — pre-trust-layer violations (tripo-wire, 4-blend
  casualty)`.
- The daemon checks trust **before every tick**: launched with
  `--apply` while trust ≠ APPLY, that tick is forced to dry-run and
  the refusal is journaled (`trust_refused_apply` + a BLOCKED grade).
- A clean cycle = zero violations, zero near-misses, zero
  UNMIRRORED_OLD findings. An unmirrored cycle resets the streak but
  does not revoke.
- `required_streak` is configurable per trust file or via
  `CLOUDEDOG_TRUST_REQUIRED_STREAK`.

### Restoring `--apply` (operator procedure)

1. Do **not** edit the trust file by hand to grant APPLY. Autonomy is
   earned by clean cycles, or it means nothing.
2. Leave the daemon running dry-run (its normal supervisor posture).
   Clean ticks accumulate: REVOKED → PROBATION → (20 clean cycles) →
   APPLY.
3. `--apply` may be restored to the supervisor only when
   `clouddog scorecard` (or the heartbeat) shows `trust_state: APPLY`
   **and** the change that restores it ships the regression tests for
   any incidents graded since the revocation (see below). On its
   first tick after restoration, if trust has dropped again, the
   daemon will refuse and dry-run that tick on its own — the flag is
   a request, trust is the authority.

## The training loop (per incident)

Every `VIOLATION` / `NEAR_MISS` record carries `regression_test` and
`fix_ref` fields (nullable in the journal). The rule:

1. **Same change**: the change that restores autonomy after an
   incident must, in that same change, (a) add a regression test that
   replays the incident and would have caught it, and (b) tighten the
   gate/trust behavior that allowed the intent to get that close.
2. **Link it**: the incident's bookkeeping entry (below, and the
   journal record when annotated) names the test and the fix PR.
3. **No orphan incidents**: `clouddog scorecard` ends with an
   "incidents missing a linked regression test" section. A non-zero
   count blocks any autonomy restoration until each incident is
   linked. (Journal records are append-only; linkage lives in this
   table + the fix PR, keyed by incident date/path.)

### Incident bookkeeping — first entries (historical, pre-trust-layer)

| Date | Incident | Grade (retroactive) | Regression test | Fix |
|---|---|---|---|---|
| 2026-10-20 ~02:52Z | Daemon `--apply` restart deleted `~/workspace/tripo-wire-20261019` (79 files) with no registry entry, no store, no claims check — the daemon's delete path bypassed every gate | VIOLATION | `tests/v1/test_delete_gate.py::test_incident_no_registry_entry_survives_daemon_apply`, `test_daemon_claimed_registered_file_survives`, `test_daemon_entry_sha_mismatch_survives`, `test_daemon_entry_copy_failing_live_verify_survives`; `tests/v1/test_daemon.py` two-pass gate tests | PR #45 (`src/clouddog/delete_gate.py` choke point at every unlink) |
| 2026-10-20 | Casualty work (4 intermediate `.blend` files) sat **local-only** in scratch and was swept — continuous-mirror never covered scratch/intermediates, and pre-claims lanes deleted without a claims check | VIOLATION (process) | `tests/v1/test_claims.py` (claims read-side, fail-closed), `tests/v1/test_delete_gate.py::test_reaper_default_claims_wiring_blocks_claimed_file`, `test_streamer_claimed_file_streams_but_never_clears`; mirror-coverage: `tests/v1/test_scorecard_trust.py` detector tests + `tests/v1/test_training_layer.py::test_daemon_tick_unmirrored_old_breaks_clean_cycle` | PR #43 (claims system) + this PR (UNMIRRORED_OLD detector — the coverage hole is now a graded failure state, not a silent gap) |

New incidents append rows here in the same PR that links their tests.

## Surfaces (all correctness-first)

- `clouddog scorecard [--root-journal PATH] [--roots a,b] [--json]` —
  the v1 card; `--root-journal ~/workspace/agent-ops/cloud-dog-audit.jsonl`
  merges guardian grades; `--roots` runs the mirror detector live.
- `cloud_dog.py scorecard [--json]` — the root card (both journals).
- Daemon heartbeat JSON: `correct_clears`, `correct_blocks`,
  `near_misses`, `violations`, `unmirrored_old`, `trust_state` before
  `freed_bytes`.
- `cloud_dog.py enforce` prints the one-line scorecard first.
- Reaper/streamer cycle summaries lead with correctness counts.

## Test isolation (hard rule)

All runtime state resolves through the one state dir. Tests must set
`CLOUDEDOG_STATE_DIR` to a tmp dir (the v1 suite does this in
`tests/v1/conftest.py`) and must **never** touch the real
`~/.clouddog` — a test that resets the real `trust.json` would fake a
revocation/promotion on the live daemon, and daemon ticks used to
overwrite the production `heartbeat.json` from pytest runs (deleter
audit, 2026-10-20).
