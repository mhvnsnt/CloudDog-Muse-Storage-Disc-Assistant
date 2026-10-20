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

## Angle C — Trust ladders / earned autonomy

Question: how do production systems grant an autonomous actor *more*
privilege over time, take it away automatically, and let it earn it back?

The sources converge on one shape, stated most explicitly in recent SRE
writing on AI agents:

- **The ladder itself:** "a graduated set of AI guardrails where each rung
  grants broader blast radius only after the agent demonstrates
  reliability at the current level: Shadow mode (read-only, no writes) →
  Supervised writes (human approval required) → Unsupervised writes
  (scoped to low-risk paths) → Full autonomy (reserved for proven,
  bounded tasks)" — with promotion implemented mechanically (in that
  write-up, as Kubernetes RBAC rebinding per rung; demotion reverses it),
  and "trust-ladder promotion should be the same typed decision, clamped
  to the ladder, not a calendar milestone" (clouatre.ca, *SRE for AI
  Agents: Error Budgets, Trust, and 90 Trials*).
- **Same ladder, incident-response form:** read-only investigation →
  recommended action → approval-gated execution → policy-bounded
  execution, where the levels "should apply to individual actions, not
  the product as a whole," and "low risk" is defined as "bounded,
  reversible, pre-approved, observable, and backed by a tested rollback"
  (Rootly, AI SRE human-in-the-loop model; Rootly architecture guide:
  "Trust is earned in stages… reserve autonomy for narrow, reversible
  failure modes that are well-instrumented and rollback-ready").
- **Promotion is earned with data:** "Each promotion is earned with data,
  not vendor confidence… the error budget becomes… an *autonomy
  throttle*. When the budget is healthy, you can afford to let the agent
  act inside its envelope; when it is nearly exhausted, you tighten the
  envelope and pull humans back into the loop" (devtocash, AI Agents for
  SRE). The Google SRE error-budget policy is the non-AI original of the
  same mechanism: budget thresholds agreed *in advance* map to automatic
  consequences — e.g. at 0% remaining, "freeze all feature deployments…
  requires CTO approval to unfreeze" (sample policy reproducing the SRE
  Workbook pattern); multi-window burn-rate alerting (14.4×/1h page,
  6×/6h page, 1×/3d ticket) supplies the *rate* signal, not just the
  total (Google SRE Workbook, as reproduced in backendbytes / isdown /
  slc.alexewerlof).
- **Governance framing:** the CSA's NIST AI RMF Agentic Profile proposes
  exactly this as a formal extension — "formal autonomy tier
  classification with corresponding oversight obligations (GOVERN
  extension)… runtime behavioral metrics, autonomy calibration
  assessment… structured incident response for agent compromise,
  behavioral drift correction, and principled agent decommissioning
  (MANAGE extension)" (Cloud Security Alliance, 2025) — over NIST AI RMF
  1.0's Govern/Map/Measure/Manage functions. Framework itself: voluntary,
  no license barrier to adapting its structure.

**Adapted to CloudDog — the ladder maps onto modes that already exist:**

| Rung | CloudDog mode | What it may do | Promotion requirement (proposed) |
|---|---|---|---|
| 0 — Shadow | `scan` / dry-run `reap` (already the default) | Observe, propose, journal intended actions; delete nothing | — (entry state; also the state every revocation lands on) |
| 1 — Supervised | `--apply` only with a live claim/approval per batch | Store + clear, one batch at a time, owner/lane-surfaced | N consecutive shadow runs whose *proposed* clears all pass the gate when replayed (proposals graded `correct`) |
| 2 — Scoped autonomy | Daemon `--apply` on allowlisted subtrees / size caps / non-game files only | Autonomous store+clear inside the scope envelope | A clean streak at rung 1 (zero `violation`, zero `near_miss` over the streak window) + scorecard reviewed |
| 3 — Full autonomy | Daemon `--apply` incl. `STORE_AND_CLEAR_STRICT` game-file bar | Everything policy allows, still gate-checked per delete | Sustained rung-2 clean record; **strict-bar deletes always keep their extra legs regardless of rung** — the ladder widens *scope*, it never removes a leg |

**Revocation (the half of the pattern people skip):**

- Any `violation` grade → **immediate drop to rung 0** for that actor,
  automatically, no human in the loop for the *demotion* (demotion is the
  safe direction). This is the error-budget freeze, applied to an actor
  instead of a release pipeline.
- `near_miss` grades burn a **violation budget** (proposed: 3 near-misses
  in a rolling 30-day window = demotion one rung + the near-miss class
  becomes a regression test before re-promotion — Angle D). This mirrors
  burn-rate thinking: single blips ticket, sustained burn pages/freezes.
- **Re-earning is a clean streak, not a timeout.** Promotion back requires
  the same evidence as the original promotion, counted from the demotion:
  shadow proposals graded, then supervised batches, then scope. A clock
  alone must never restore privilege (the clouatre.ca point: promotion is
  a typed, evidence-gated decision, "not a calendar milestone").
- Trust state is **per actor** (daemon / reaper / streamer / a named lane
  invocation), stored in the registry next to the scorecard — one actor's
  violation must not demote an unrelated actor, and one actor's clean
  streak must not launder another's record.

**Mechanism this shapes:** the trust ladder (rungs = existing CloudDog
modes), automatic revocation on `violation`, near-miss budget, and
clean-streak re-earning, all driven by the Angle B scorecard.

## Angle D — RLHF-style feedback, adapted to a deterministic tool

Question: CloudDog has no weights to update. What does "training" mean
for it? The RLHF literature, read carefully, decomposes into four parts —
and three of them translate directly:

1. **A reward signal collected from human judgment.** In RLHF, humans
   rank outputs; a reward model learns to predict the ranking; the policy
   is optimized against it (InstructGPT, arXiv 2203.02155, as summarized
   in srdas.github.io/NLPBook and the RLHF survey literature). CloudDog's
   analogue: the **scorecard is the reward model** — except it needs no
   learning, because its judgments are mechanical (gate legs, live verify
   results, claim state). Human judgment enters where it should: the
   owner/lane reviews the scorecard and adjudicates disputed grades
   (was that block correct? was that clear actually safe?), and those
   adjudications are data (see 3).
2. **Reward shaping — and its known failure.** Adding intermediate
   rewards for steps toward the goal speeds learning but risks changing
   what is optimized. The canonical warning is the one this whole doc
   started with: proxy ≠ objective (Goodhart / specification gaming,
   Amodei et al. 2016). **Direct rule for CloudDog: never reward a proxy
   again.** Bytes freed, files cleared, and GB reclaimed must be
   *reported* (they're useful telemetry) but must never appear in any
   grade, promotion criterion, dashboard headline, or guardian-cron
   success condition. The only positively graded outcomes are
   *verified-safe* ones: a clear whose pull-back was proven, a block that
   correctly refused, a mirror that completed byte-identical. If a number
   can go up by deleting something unstored, it is a shaping bug.
3. **The feedback dataset = regression-test-per-incident.** In
   deterministic-land, "training data" is a test corpus. The production
   pattern from Angle B — extract every production failure into the eval
   suite as a regression test (Langfuse→promptfoo workflow) — becomes a
   standing rule: **no incident is closed until a test exists that fails
   on the pre-fix code and passes on the fixed code**, named for the
   incident. CloudDog already has the first two entries of this corpus by
   history: (a) *delete with no registry entry must be blocked*
   (tripo-wire, PR #45) and (b) *delete of a claimed / in-progress path
   must be blocked* (claims, PR #43). The rule generalizes them from
   fixes into a curriculum: every future `violation`/`near_miss` adds its
   scenario (synthetic tree + registry/claims state + expected grade) to
   the corpus, and CI runs the full corpus on every PR — the promptfoo
   pass-rate gate, with a 100% bar for safety cases.
4. **Punishment = privilege loss, not scolding.** RLHF's negative reward
   becomes, deterministically, Angle C's revocation: a `violation`
   doesn't produce a log line and a shrug, it *changes the actor's future
   action space* (rung drop → shadow mode). That is the only "punishment"
   a deterministic system can actually feel, and it is sufficient —
   coupled with (3), the system both *can't* repeat the act at its old
   privilege and *is tested* against repeating it forever.

What does **not** translate, honestly: online learning, exploration,
PPO, preference modeling. CloudDog should never learn online from its own
deletions; its "policy updates" are code changes reviewed by humans
through PRs, exactly as `delete_gate.py` was. Anyone proposing an ML
layer here is solving a problem CloudDog does not have.

**Mechanism this shapes:** scorecard-as-reward (with the no-proxy rule),
the regression-test-per-incident corpus as the training set, and
revocation (Angle C) as the negative reward.

## Angle E — Guardrails / blast-radius patterns

Question: what structural patterns keep one bad run from becoming a bad
day? Six patterns, each with a production pedigree and a CloudDog form:

1. **Choke-point enforcement.** All destructive paths route through one
   gate that the actor cannot opt out of — the audit literature makes the
   same point for logging: "a recorder inside the agent process depends
   on the agent calling it… recording at a boundary outside the agent
   process, one the agent cannot opt out of, is what makes an audit trail
   hold up" (pipelab.org, *What a Hash Chain Can't Prove*). CloudDog:
   **already landed** — `delete_gate.py` (PR #45) is exactly this, born
   from the daemon's parallel delete authority bypassing it. Remaining
   work: keep a CI-level invariant test that greps for unlink sites and
   asserts each is gate-mediated (the choke point must be *defended*, not
   just built).
2. **Fail-closed + blast-radius caps.** Autoremediation governance in
   SRE practice is "explicit allowlists, action tiers, preconditions,
   success signals, stop conditions, rollback paths, and approval rules…
   autonomy should never mean open-ended action" (Rootly AI SRE FAQ);
   guardrail lists add "strict rate limits… circuit breakers and loop
   detection… action tiers" and a "red-button" revocation control
   (dev.to, SRE AI Agent Safe Failure). CloudDog forms: per-run delete
   caps (count + bytes) as a circuit breaker in Nygard's sense — the
   breaker "trips, and all further calls return with an error, without
   the protected call being made at all," with a monitor alert on trip
   (Fowler, *CircuitBreaker*, after Nygard, *Release It!*) — plus the
   existing per-file gate. A run that hits its cap stops and journals;
   it does not slow down and continue.
3. **Two-key / two-person rule for the irreversible tier.** Separation of
   duties in NIST SP 800-12: "dividing roles and responsibilities so that
   a single individual cannot subvert a critical process" — one actor
   initiates, another authorizes (csrc.nist.rip, SP 800-12 ch. 10);
   two-person control generalizes it: a sensitive task "requires two
   persons to action it" (geekmungus, summarizing the CISSP body of
   knowledge). CloudDog already has this in law form for game files
   (store + retrievability + **owner surfaced**, `STORE_AND_CLEAR_STRICT`).
   Generalize the pattern one step: the *first* clear at a newly promoted
   rung, and any clear above a size threshold, require two independent
   signals — e.g. gate-pass **plus** a fresh claim-free re-check by a
   second code path (reaper verifying the daemon's proposal), not the
   same process re-reading its own cache.
4. **Dry-run probation.** Every new actor, new scope, or re-earning actor
   starts in shadow/dry-run (Angle C, rung 0–1). This is the SRE
   "shadow mode catches what would have gone wrong" result: in the
   clouatre.ca experiment, shadow comparison flagged the majority of an
   incorrect proposed change set before it could act. CloudDog's dry-run
   default already encodes the instinct; the ladder makes it a *stage
   with exit criteria* instead of a flag.
5. **Kill switch.** A single, tested, boring mechanism: a sentinel file
   / config flag that every actor checks before any delete and that the
   guardian cron can set; when set, all actors drop to rung 0 and journal
   the stop. Requirements from the pattern's logic: it must be checkable
   *at the choke point* (delete time, not startup), and setting it must
   not require stopping a process that might be mid-delete (the
   NEVER-KILL-IN-PROGRESS law cuts both ways — the switch stops *new*
   deletes; an in-flight verified delete finishes).
6. **Append-only audit journal with actor attribution + hash chain.**
   Current `audit.py` is append-only and fsync'd — keep that — and add:
   - **`actor` on every event** (daemon/reaper/streamer/CLI + invocation
     id), plus `grade` and `gate legs` fields for delete-class events.
     The agent-ops definition of a tamper-evident trail requires exactly
     this content: "who (which agent identity), what (the action and its
     parameters), when (a trusted timestamp), and under what authority
     (the policy and approval that permitted it)" (cloudthinker.io).
   - **Hash chaining**: each record stores the previous record's hash;
     its own hash covers pointer + contents, with canonical JSON
     serialization (`sort_keys`, fixed separators) so verification is
     stable (dev.to, hash-chained audit log walkthrough; SOC 2 audit-log
     write-up: `record[n].hash = H(payload[n] || record[n-1].hash)`,
     "editing one record is… impossible to hide" *within the chain*).
   - **Honest limit, stated in the sources and repeated here so nobody
     over-claims it:** a bare hash chain "only proves the file agrees with
     itself" — anyone holding the file can rewrite it and recompute the
     tail (pipelab.org; medium, *Your audit log is a promise*). The fix
     is an **external anchor**: periodically copy the chain head (hash +
     length) somewhere the actor can't rewrite — for CloudDog, the
     natural anchor is a commit to this repo / the existing agent-ops log
     on the guardian's cadence. Chain + anchored head = tamper-*evident*;
     chain alone = a consistency check. Build both or claim neither.

**Mechanism this shapes:** journal attribution (`actor`/`grade`/legs),
hash-chain + external head anchor, per-run circuit-breaker caps,
two-signal rule for the irreversible tier, and a choke-point kill switch.
