# CloudDog Governance & Training — Research Consensus

**Status: PROPOSED — not yet approved.** Current code behavior stays exactly
as-is until the owner approves a change. This document records multi-angle
research (owner correction 2026-10-20: CloudDog was "fed" for deleting —
bytes freed was the reward metric — instead of being trained on correct
behavior; two deletions of unstored work resulted: a Tekken 5 ISO partial
and the tripo-wire folder).

Written for direct application to CloudDog as it actually is: a
deterministic, stdlib-only Python CLI/daemon (`src/clouddog/`, ~5,300 LOC).
The recurring verdict of this research is stated up front, plainly:
**adapt the patterns in stdlib; pull almost nothing.** Every heavy system
below solves a bigger problem than CloudDog has, and each one would break
the stdlib-only rule that keeps CloudDog auditable by one reader.

## Why this doc exists — the reward was wrong, not just the code

The two 2026-10-20 deletions were not random bugs. They were the system
optimizing the metric it was fed:

- The daemon's `--apply` path deleted `~/workspace/tripo-wire-20261019`
  (79 files, 1,068,247,040 B) with no store destination and no registry
  entry — documented in the header of `src/clouddog/delete_gate.py`
  (PR #45, merged at `3e64090`).
- A Tekken 5 ISO partial was deleted in the same period (see
  `docs/LAWS.md`, NEVER-KILL-IN-PROGRESS / NEVER-DELETE-GAME-FILES).

This is the textbook failure the AI-safety literature calls **reward
hacking / specification gaming**: an optimizer "achieving the literal,
formal specification of an objective — without actually achieving an
outcome that the programmers intended," strongly associated with
**Goodhart's law** — "when a measure becomes a target, it ceases to be a
good measure" (Reward hacking, Wikipedia, summarizing Amodei et al. 2016,
"Concrete Problems in AI Safety"). Bytes freed was the proxy; *the owner's
work being safe on a store* was the true objective. The proxy and the true
objective diverged, and the system followed the proxy.

Consequence for design: fixing the delete path (done — `delete_gate.py`)
is necessary but not sufficient. The **feedback system itself** has to be
rebuilt so that:

1. The rewarded outcome is *verified-safe storage*, never bytes freed.
2. Every action is **graded**, not just counted (Angle B).
3. Autonomy is **earned, revocable, and re-earned** (Angle C).
4. Every incident becomes a **permanent regression test** — the
   deterministic equivalent of training data (Angle D).
5. Enforcement sits at a **choke point** the actor cannot route around,
   with an audit journal that attributes every action to an actor
   (Angle E).

## Where CloudDog already stands (code, read 2026-10-20 at `3e64090`)

| Piece | File | What it already does | Gap this research addresses |
|---|---|---|---|
| Policy engine | `src/clouddog/policy.py` | Hand-rolled rule engine: ordered rules → `Action` enum (`STORE`, `STORE_AND_CLEAR`, `STORE_AND_CLEAR_STRICT`, `KEEP_LOCAL`, `SACRED`), stdlib only, laws verbatim-grounded in its docstring | Rules are code, not data; no decision log explaining *which rule fired* per path in a queryable form |
| Delete choke point | `src/clouddog/delete_gate.py` | Single gate, 4 legs (registry binding, live verify this run, claims readable-or-fail-closed, fresh policy), fail-closed, blocks journaled with per-leg reasons | Gate returns allow/block; it does not *grade* the actor or feed a scorecard |
| Work claims | `src/clouddog/claims.py` | SQLite leases with fencing epochs + TTL/heartbeat (PR #43) | Claims say "someone is working here"; they do not say *who did what* historically |
| Audit journal | `src/clouddog/audit.py` | Append-only JSONL, fsync'd, `AuditEvent{ts, kind, path, size, locator, note}` + markdown rendering | **No actor field. No hash chain. No grade.** An event cannot be attributed to daemon vs reaper vs streamer vs a named lane, and a rewritten journal is undetectable |
| Registry | `src/clouddog/registry.py` | SQLite record of stored files | No scorecard / trust-state tables |

The research below is organized as five angles (A–E), matching the five
design needs above. Each angle ends with the specific CloudDog mechanism
it should shape. A consolidated candidate table (license + verdict) and a
consensus recommendation close the doc.
