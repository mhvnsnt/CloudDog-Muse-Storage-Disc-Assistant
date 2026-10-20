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

## Angle A — Policy engines as law-enforcement

Question: should CloudDog's laws live in an external policy engine, or in
code? The production systems split into three shapes: **sidecar/daemon**,
**embedded library**, and **rules-as-data in the host language**.

| Candidate | License (source-checked) | Runtime shape | Fit for stdlib Python CLI/daemon |
|---|---|---|---|
| **OPA / Rego** (Open Policy Agent, CNCF graduated 2021) | Apache-2.0 — "written in Golang… licensed under the Apache License 2.0"; CNCF graduated (thomasvanlaere.com; cncf.io/projects/open-policy-agent-opa) | Go binary as sidecar/daemon (`opa run --server`, REST decision API), or embedded **only in Go programs** via `github.com/open-policy-agent/opa/rego` (openpolicyagent.org/docs — Integrating OPA) | **Skip as a dependency.** No Python embedding path; running it means a second daemon, a second language (Rego, Datalog-style, "steep" learning curve per permit.io/osohq comparisons), and a network hop on the delete path — the exact path that must fail closed when anything is unreachable. Its *pattern* is the prize, not the binary (see below). |
| **AWS Cedar** | Apache-2.0 — "free, open-source, licensed under the Apache 2.0 license… written in Rust" (jun.codes); CNCF Sandbox, accepted 2025-10-08 (cncf.io/projects/cedar) | In-process library — Rust core (`cedar-policy` crate), Go implementation (`cedar-go`, Apache-2.0); designed `permit(principal, action, resource) when {…}` / `forbid`, default-deny, "policies never authorize an action by default — it must be explicitly permitted," formally verified spec + differential random testing vs the Rust implementation (Cedar paper, arXiv 2403.04651 / FSE'24 2407.01688) | **Skip as a dependency; adapt its grammar.** No mature first-party Python binding was found in the sources reviewed (the ecosystem itself notes Python/TS bindings are still wanted — bundle.app interview). But Cedar's *shape* is exactly CloudDog's delete question: `permit(actor, delete, path) when {verified && retrievable} unless {claimed \|\| sacred}`. That principal–action–resource + explicit-permit + forbid-trumps-permit shape should be the template for how `policy.py` rules are written and named. |
| **Casbin** (pycasbin) | Apache-2.0 — "This project is licensed under the Apache 2.0 license" (pypi.org/project/pycasbin) | Embedded Python library; PERM metamodel — model in a `.conf`, policies in CSV/DB; ACL/RBAC/ABAC/deny-override/priority models (PyPI; Apache Incubator proposal) | **Skip.** The one candidate that *could* be pip-installed into Python — and still the wrong trade: it answers "may subject X do action Y on object Z" for many subjects, while CloudDog has essentially one subject (itself, in a few modes) and its hard questions are *temporal and evidentiary* (verified *this run*? claim *still live*? bytes *still bind*?). Casbin has no native vocabulary for that, so the real logic would still live in Python around it — a dependency bought for the easy 10%. |
| **durable_rules** | License not stated in the PyPI pages reviewed — **unverified, do not pull without checking the repo LICENSE first** | Python, but with a **C core engine** (Rete forward-chaining), historically Werkzeug + Redis for state (pypi.org/project/durable-rules) | **Skip.** A compiled C extension + forward-chaining inference engine for a rule set that today fits in one ordered function. Rete pays off at thousands of rules/facts; CloudDog has ~a dozen rules over one path at a time. It also breaks the "read the whole engine in one sitting" auditability that made `delete_gate.py` reviewable. |
| **Small Python rule libs** (`simple-rule-engine`, `light-rule-engine`, etc.) | MIT (light-rule-engine, PyPI: "licensed under the MIT License") | Embedded, decision-table style | **Skip.** They reproduce what `policy.py` already is, with less domain fit and an external maintainer. The decision-table *idea* is useful for tests (Angle B), not as a runtime dep. |
| **What CloudDog already has** (`policy.py` + `delete_gate.py`) | Own code | Embedded, stdlib, ordered rules, fail-closed gate | **Keep — and borrow three patterns** (below). |

**Patterns to adapt from this angle (no code pulled):**

1. **Decouple decision from enforcement (OPA's core architecture).**
   OPA's value proposition in every source is the same: policy *decisions*
   are produced separately from the code that *enforces* them, and
   decisions are **logged**. CloudDog already has this split in embryo
   (`policy.evaluate()` decides; daemon/reaper/streamer enforce;
   `delete_gate` re-decides at delete time). Complete it: every
   `evaluate()` and every gate verdict should emit a **decision record**
   (input facts, rule/action returned, gate legs, actor, timestamp) into
   the audit journal — OPA calls this a decision log; for CloudDog it is
   the raw material of the scorecard in Angle B.
2. **Default-deny + forbid-trumps-permit (Cedar).** Cedar is
   default-deny by construction and its spec is *proved* to satisfy
   "deny trumps allow." CloudDog's equivalent already exists for deletes
   (gate: any missing leg = blocked). Extend the convention explicitly:
   in `policy.py`, `SACRED` and any gate block must be documented and
   tested as *trumping* any `STORE_AND_CLEAR` outcome, in that order, with
   a regression test per trump pair.
3. **Policies as reviewable data for the *test* layer, code for the
   *runtime* layer.** Rego/Cedar/Casbin all externalize policy so it can
   be diffed and tested without touching enforcement code. CloudDog's
   laws change rarely and are verbatim owner law (`docs/LAWS.md`), so
   runtime-as-code stays. But the *eval corpus* (Angle B) should be data:
   a JSONL/table of (facts → expected action / expected gate verdict)
   that CI runs against the real `policy.py` — the Casbin model/policy
   split, applied to tests instead of runtime.

**Mechanism this shapes:** `policy.py` stays stdlib code; add (a) a
decision-record emission on every evaluate/gate call and (b) a data-driven
policy test corpus in CI. No policy-engine dependency is pulled.

## Angle B — Eval / scorecard harnesses for autonomous actors

Question: how do production agent systems *grade* actions, persist the
grades, and surface them — so "is the actor behaving?" is a query, not a
vibe?

What the sources show, consistently:

- **Inspect (UK AI Security Institute)** — MIT ("AISI published Inspect
  under an MIT open source licence," interoperable-europe.ec.europa.eu;
  TechRepublic confirms MIT). Its whole architecture is three parts:
  **Datasets** (samples/scenarios), **Solvers** (the actor under test),
  **Scorers** (grade the output; built-ins return `CORRECT`/`INCORRECT`
  with accuracy/stderr metrics), producing **Eval Logs** that can be
  re-scored later with `inspect score` (inspect.aisi.org.uk). Limits
  (tokens/time/tool calls) are recorded *as part of the result*, because
  "a result is only interpretable next to the limits that produced it"
  (AISI engineering playbook).
- **promptfoo** — MIT (multiple sources; its own README via toolerific:
  "Open source: MIT licensed"). Declarative YAML test suites run in CI;
  the gate form is explicit: `promptfoo check --pass-rate 0.92` "exits
  non-zero if the pass rate dropped, blocking the PR" (callsphere.ai CI
  gate walkthrough). Assertions include deterministic checks first
  (contains / latency / cost thresholds), with LLM-rubric checks only
  where determinism can't reach.
- **Deterministic-first grading** is the stated principle in independent
  harness write-ups: "measure what you can measure deterministically, and
  only reach for an LLM judge where you must… free, instant, and
  reproducible" (dev.to, Scoring AI Agents). The harness emits
  `report.json` + `failures.json` + appends to `history.jsonl` "so you can
  track drift over time."
- **Scorecards by slice, not one global number:** "Policy, Safety,
  Utility, Latency, Economics reported by intent"; anti-patterns called
  out are "one global benchmark (hides per-intent regressions),"
  "final-answer-only evals (miss bad plans that got lucky — grade the
  *trace*)," and "auto-promotion" (contextosai.com, agent-harness
  build plan).
- **Observability in production (Langfuse pattern)** — core MIT
  (turingpost: "The core is MIT-licensed; enterprise-only modules…
  licensed separately"). Traces + scores persist per run; and the key
  workflow: "once you identify a production failure… extract that failure
  scenario and add it to [the] test suite as a regression test, ensuring
  it cannot recur" (dev.to, Langfuse vs Promptfoo) — see also Angle D.
- **Harness AI Evals (commercial)** — the same shape at platform scale:
  eval suites as a pipeline step; "when a quality threshold regresses, the
  build fails exactly the way a unit-test failure does," datasets/metrics/
  thresholds as config with RBAC + audit (harness.io). Verdict on all
  hosted platforms for CloudDog: **skip** — a single-host stdlib daemon
  needs no SaaS and must not phone its grades out.

**The grade vocabulary.** Inspect's `CORRECT`/`INCORRECT` is too coarse
for an actor whose *blocked* actions are successes (a gate block is the
system working). Proposed CloudDog grades, one per *attempted action*,
recorded whether or not the action happened:

| Grade | Meaning | Example |
|---|---|---|
| `correct` | Action taken, all legs held, outcome verified after the fact | Store→live-verify→clear, pull-back hash matches |
| `blocked` | Action attempted, gate/policy refused, actor complied | Gate blocks an unregistered path; actor journals the block and moves on — **this is a good outcome, score it positively** |
| `violation` | Action taken that should have been refused, or a refusal bypassed | The tripo-wire delete: no registry entry, delete happened anyway |
| `near_miss` | Action taken that was legal only by luck — a leg was stale, cached, or checked against the wrong object, but no loss occurred | Delete based on last run's verify flag instead of a live verify |
| `error` | Action failed mechanically (network, store down) with no policy implication | Upload timeout, retried per `retry.py` |

Two design rules follow from the sources: (1) **grade the trace, not just
the outcome** — a `correct`-looking clear whose verify was stale is a
`near_miss`, not a `correct` (this is exactly the "bad plan that got
lucky" anti-pattern); (2) **never collapse to one number** — the
scorecard is reported per actor × per action-kind × rolling window, so a
streamer regression can't hide inside daemon success.

**Persistence + surfacing (adapted, stdlib):** a `scorecard` table in the
existing SQLite registry (alongside `claims`), one row per graded action
keyed to the audit-journal event; a `clouddog score` CLI that renders the
rolling scorecard (counts + rates per grade, per actor, last N runs /
30 days) as text + JSON — the Inspect "eval log + view" and promptfoo
"report.json + check" pair, collapsed into the tool CloudDog already is.
CI gate form: the policy/gate test corpus (Angle A.3) must pass at 100%
for `violation`-class cases before merge — pass-rate thresholds are for
quality evals; for *safety* cases the only acceptable rate is total.

**Mechanism this shapes:** the scorecard grades (`correct` / `blocked` /
`violation` / `near_miss` / `error`), their persistence in the registry,
and the `clouddog score` surface. No eval framework is pulled — Inspect
and promptfoo are the *pattern* references (both MIT, both noted here if
a future non-stdlib eval runner is ever wanted for offline analysis).
