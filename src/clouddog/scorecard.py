"""Scorecard — CloudDog is graded on CORRECTNESS, not bytes freed.

Owner law (2026-10-20): the old feedback loop rewarded deletion (bytes
freed went up, everyone cheered). That trained the exact behavior that
deleted unregistered work. This module is the replacement feedback
loop. Every daemon / reaper / streamer / guardian action is graded and
permanently journaled in the audit journal (``~/.clouddog/audit.jsonl``,
kind ``scorecard``):

  CORRECT       — stored + live-verified + lawfully cleared through the
                  delete gate, or correctly stored-and-kept, or
                  correctly kept local. GOOD: feeds trust.
  BLOCKED       — a gate or policy stopped a bad action and more than
                  one leg/verdict stood behind the stop (or a trust /
                  policy gate refused outright). GOOD behavior, counted
                  as such: the system working as designed.
  NEAR_MISS     — a delete intent reached the delete gate and was
                  stopped by exactly ONE failing leg. It would have
                  been a violation but for that gate. STARVES trust:
                  revokes earned autonomy on the spot.
  VIOLATION     — a delete executed without full gate proof. Post-PR#45
                  this must stay impossible; if evidence of one ever
                  appears in the journal it is graded VIOLATION and
                  revokes trust on the spot.
  UNMIRRORED_OLD — a mirror-coverage FAILURE STATE (not a grade of a
                  delete): work/scratch/intermediate files older than a
                  configurable age with no registry entry and no store
                  record. The casualty work of 2026-10-20 sat local-only
                  because continuous-mirror never covered scratch. A
                  cycle containing these findings is not clean.

Every human-readable surface this module produces leads with
correctness; ``freed_bytes`` is a footnote, printed last.

VIOLATION / NEAR_MISS records carry ``regression_test`` and ``fix_ref``
fields (nullable). The training-loop SOP (docs/TRAINING-LOOP-SOP.md)
requires every such incident to ship a regression test + a gate/trust
tightening in the same change that restores autonomy; the scorecard
report flags graded incidents missing a linked test.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from clouddog import trust as _trust

__all__ = [
    "CORRECT",
    "BLOCKED",
    "VIOLATION",
    "NEAR_MISS",
    "UNMIRRORED_OLD",
    "GRADES",
    "SCORECARD_KIND",
    "DEFAULT_UNMIRRORED_AGE_S",
    "SCRATCH_COMPONENTS",
    "ROOT_ACTION_GRADES",
    "ScorecardSummary",
    "classify_gate_verdict",
    "classify_delete_outcome",
    "record_grade",
    "grade_gate_verdict",
    "detect_unmirrored_old",
    "adapt_root_event",
    "adapt_root_events",
    "read_root_journal",
    "summarize_events",
    "summarize_journal",
    "format_scorecard",
    "scorecard_line",
]

CORRECT = "CORRECT"
BLOCKED = "BLOCKED"
VIOLATION = "VIOLATION"
NEAR_MISS = "NEAR_MISS"
UNMIRRORED_OLD = "UNMIRRORED_OLD"
GRADES = (CORRECT, BLOCKED, VIOLATION, NEAR_MISS, UNMIRRORED_OLD)

#: Journal kind for graded records.
SCORECARD_KIND = "scorecard"

#: Grades that revoke earned autonomy the moment they are graded.
REVOKING_GRADES = (VIOLATION, NEAR_MISS)

#: Default age after which an unmirrored scratch/intermediate file is a
#: system failure state (6h, configurable).
DEFAULT_UNMIRRORED_AGE_S = 6 * 3600

#: Path components (relative to a watched root, or the root's own name)
#: that mark work/scratch/intermediate locations — the places
#: continuous-mirror historically never covered.
SCRATCH_COMPONENTS = frozenset({
    "scratch", "intermediate", "intermediates", "wip", "work",
    "working", "staging", "in-progress", "in_progress", "temp",
    "tmp", "partials", "partial", "renders-in-progress",
})

_IN_FLIGHT_SUFFIXES = (".part", ".aria2", ".kaggle-partial", ".tmp", ".downloading")


# ---------------------------------------------------------------- grading


def classify_gate_verdict(verdict) -> str:
    """Grade a delete-gate verdict.

    allowed -> CORRECT. Blocked by exactly ONE failing leg -> NEAR_MISS
    (that single leg is all that stood between the intent and a
    violation). Blocked by multiple legs -> BLOCKED (the defense in
    depth held with margin — good behavior, counted as such).

    Exception: the daemon's two-pass deferral (a just-registered store
    whose delete is deferred to a later pass) is designed, correct
    behavior — graded BLOCKED, never a near-miss.
    """
    if getattr(verdict, "allowed", False):
        return CORRECT
    blocks = tuple(getattr(verdict, "blocks", ()) or ())
    for b in blocks:
        if "two-pass" in str(getattr(b, "reason", "") or ""):
            return BLOCKED
    if len(blocks) == 1:
        return NEAR_MISS
    return BLOCKED


def classify_delete_outcome(deleted: bool, verdict=None) -> str:
    """Grade an executed (or attempted) delete.

    A delete that executed without a fully-passing gate verdict is a
    VIOLATION. A delete that executed through a passing gate is
    CORRECT. A delete that did not execute defers to the verdict grade.
    """
    if deleted:
        if verdict is not None and getattr(verdict, "allowed", False):
            return CORRECT
        return VIOLATION
    if verdict is not None:
        return classify_gate_verdict(verdict)
    return BLOCKED


def record_grade(
    journal,
    grade: str,
    *,
    path: str = "",
    source: str = "",
    size: int | None = None,
    locator: str | None = None,
    note: str = "",
    blocks=(),
    regression_test: str | None = None,
    fix_ref: str | None = None,
    trust_path: str | None = None,
    revoke_on_bad: bool = True,
) -> dict:
    """Journal one graded record (kind ``scorecard``) and, for
    VIOLATION / NEAR_MISS, revoke trust on the spot (persisted
    immediately). ``journal`` is an AuditJournal-like object with
    ``record(event)``, or None (the record dict is still returned).

    Returns the record dict that was journaled.
    """
    grade = str(grade or "").upper()
    if grade not in GRADES:
        raise ValueError(f"unknown grade: {grade!r}")
    block_dicts = [
        {"leg": str(getattr(b, "leg", "") or ""), "reason": str(getattr(b, "reason", "") or "")}
        if not isinstance(b, dict) else dict(b)
        for b in (blocks or ())
    ]
    record = {
        "kind": SCORECARD_KIND,
        "grade": grade,
        "path": path,
        "source": source,
        "size": size,
        "locator": locator,
        "note": note,
        "blocks": block_dicts,
        "regression_test": regression_test,
        "fix_ref": fix_ref,
    }
    if journal is not None:
        try:
            from clouddog.audit import AuditEvent

            event = AuditEvent(
                kind=SCORECARD_KIND,
                path=path,
                size=size,
                locator=locator,
                note=note,
                grade=grade,
                source=source,
                blocks=tuple(block_dicts),
                regression_test=regression_test,
                fix_ref=fix_ref,
            )
        except ImportError:
            event = record  # type: ignore[assignment]
        try:
            journal.record(event)
        except Exception:  # noqa: BLE001 - grading must never break a lane
            pass
    if revoke_on_bad and grade in REVOKING_GRADES:
        try:
            _trust.revoke(
                trust_path,
                reason=f"{grade} graded ({source or 'unknown source'}): "
                       f"{path} — {note}".strip(),
            )
        except Exception:  # noqa: BLE001 - revocation is best-effort here;
            pass  # the journaled grade is the permanent evidence
    return record


def grade_gate_verdict(
    journal,
    verdict,
    *,
    path: str = "",
    source: str = "",
    size: int | None = None,
    locator: str | None = None,
    note: str = "",
    trust_path: str | None = None,
    regression_test: str | None = None,
    fix_ref: str | None = None,
) -> str:
    """Classify a gate verdict, journal the grade, revoke on bad grades.

    Returns the grade string.
    """
    grade = classify_gate_verdict(verdict)
    record_grade(
        journal,
        grade,
        path=path,
        source=source,
        size=size,
        locator=locator,
        note=note or getattr(verdict, "summary", ""),
        blocks=tuple(getattr(verdict, "blocks", ()) or ()),
        regression_test=regression_test,
        fix_ref=fix_ref,
        trust_path=trust_path,
    )
    return grade


# ------------------------------------------------------- mirror coverage


def _has_marker_up_to(path: str, root: str) -> bool:
    """True when a .DO_NOT_DELETE* marker sits in the file's directory or
    any parent up to (and including) the watched root."""
    current = os.path.dirname(os.path.abspath(path))
    stop = os.path.abspath(root)
    while True:
        try:
            for name in os.listdir(current):
                if name.startswith(".DO_NOT_DELETE"):
                    return True
        except OSError:
            pass
        if current == stop or not current.startswith(stop + os.sep):
            return False
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def detect_unmirrored_old(
    roots,
    *,
    age_s: int = DEFAULT_UNMIRRORED_AGE_S,
    now: float | None = None,
    registry_lookup=None,
    store_lookup=None,
    claim_check=None,
    max_findings: int = 500,
    max_depth: int | None = None,
) -> list[dict]:
    """Find UNMIRRORED_OLD files: under ``roots``, in work/scratch/
    intermediate locations, older than ``age_s``, with NO registry entry
    and NO store record.

    Protections respected (never findings): fresh mtimes (younger than
    ``age_s``), live work claims, .DO_NOT_DELETE markers, in-flight
    suffixes, symlinks, and anything with a registry/store record.
    ``registry_lookup`` / ``store_lookup`` are callable(path) -> truthy
    record or None; ``claim_check`` is callable(path) -> (blocked,
    detail). With no lookup wired, no file can prove coverage, so old
    scratch files are findings (fail-visible, never fail-silent).
    """
    now = time.time() if now is None else now
    findings: list[dict] = []
    for root in roots or []:
        root_abs = os.path.abspath(str(root))
        root_name = os.path.basename(root_abs).lower()
        for dirpath, dirnames, filenames in os.walk(root_abs, followlinks=False):
            rel_dir = os.path.relpath(dirpath, root_abs)
            rel_parts = [] if rel_dir == "." else rel_dir.split(os.sep)
            if max_depth is not None and len(rel_parts) >= max_depth:
                dirnames[:] = []  # bounded walk: scratch lives near the top
            components = {p.lower() for p in rel_parts} | {root_name}
            if not (components & SCRATCH_COMPONENTS):
                continue
            for name in filenames:
                if len(findings) >= max_findings:
                    return findings
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    continue
                if name.lower().endswith(_IN_FLIGHT_SUFFIXES):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                age = now - st.st_mtime
                if age < age_s:
                    continue  # fresh: still in flight, not a finding
                if registry_lookup is not None:
                    try:
                        if registry_lookup(full):
                            continue
                    except Exception:  # noqa: BLE001 - unreadable = no record
                        pass
                if store_lookup is not None:
                    try:
                        if store_lookup(full):
                            continue
                    except Exception:  # noqa: BLE001
                        pass
                if claim_check is not None:
                    try:
                        claimed, _detail = claim_check(full)
                    except Exception:  # noqa: BLE001 - unreadable claims:
                        claimed = True  # protected until proven otherwise
                    if claimed:
                        continue
                if _has_marker_up_to(full, root_abs):
                    continue
                findings.append({
                    "path": full,
                    "size": st.st_size,
                    "age_s": int(age),
                    "root": root_abs,
                    "reason": (
                        f"unmirrored: {int(age // 3600)}h old in a "
                        "scratch/intermediate location with no registry "
                        "entry and no store record"
                    ),
                })
    return findings


# ------------------------------------------------- guardian / root adapter
#
# The guardian cron runs the ROOT cloud_dog.py `enforce --apply`, whose
# actions never pass through clouddog.delete_gate — they are gated by
# evaluate() + the laws engine (decide_delete) + live verify, and
# journaled to the root audit log (~/workspace/agent-ops/
# cloud-dog-audit.jsonl, events keyed by "action", fail-closed all
# morning of the 2026-10-20 incidents per the deleter audit). Those
# actions are graded HERE as a first-class source: the mapping below
# converts root audit events into scorecard records. Root deletes that
# executed are CORRECT only because reap_v2's own flow live-verifies
# before unlink; a root "reap" event IS the post-verify record. (If a
# root event ever showed a delete with no preceding verify, that is
# graded by classify_delete_outcome as a VIOLATION at the call site
# that detects it — the adapter does not invent evidence either way.)

#: root audit action -> (grade, is_clear, note)
ROOT_ACTION_GRADES = {
    "reap": (CORRECT, True, "cleared via root reap (live-verified before unlink)"),
    "reap_policy_block": (BLOCKED, False, "root policy/laws gate blocked a delete"),
    "reap_keep": (BLOCKED, False, "root live verify failed; kept local"),
    "reap_policy_skip": (CORRECT, False, "kept local: root evaluate() not reap-eligible"),
    "reap_retrievability_skip": (CORRECT, False, "kept local: strict-bar proof not attempted"),
    "reap_policy_unavailable": (BLOCKED, False, "root reap refused: policy engine unavailable (fail closed)"),
    "mirror_part_unverified": (BLOCKED, False, "root mirror part failed verification; kept local"),
}


def adapt_root_event(event: dict) -> dict | None:
    """Convert one root cloud_dog.py audit event into a scorecard
    record dict, or None when the action is not gradeable (summaries,
    cycle bookkeeping, proofs with no verdict yet)."""
    if not isinstance(event, dict):
        return None
    action = str(event.get("action") or "")
    if action == "verify":
        ok = bool(event.get("ok"))
        grade, is_clear, note = (
            (CORRECT, False, "root verify passed")
            if ok else
            (BLOCKED, False, "root verify failed; kept local")
        )
    elif action == "reap_retrievability_proof":
        if event.get("proven"):
            grade, is_clear, note = CORRECT, False, "root pull-back proof passed"
        else:
            grade, is_clear, note = BLOCKED, False, "root pull-back proof failed; strict bar kept local"
    elif action in ROOT_ACTION_GRADES:
        grade, is_clear, note = ROOT_ACTION_GRADES[action]
    else:
        return None
    rec = {
        "kind": SCORECARD_KIND,
        "grade": grade,
        "path": str(event.get("path") or ""),
        "source": "guardian-root",
        "size": event.get("size"),
        "locator": None,
        "note": ("cleared " if is_clear else "") + note,
        "blocks": [],
        "regression_test": None,
        "fix_ref": None,
        "ts": event.get("ts", ""),
    }
    if is_clear and isinstance(event.get("size"), int):
        rec["_freed"] = int(event["size"])
    return rec


def adapt_root_events(events) -> list[dict]:
    out = []
    for e in events or []:
        rec = adapt_root_event(e)
        if rec is not None:
            out.append(rec)
    return out


def read_root_journal(path: str, limit: int = 100000) -> list[dict]:
    """Read the root script's audit journal (JSONL keyed by 'action')."""
    import json as _json

    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(_json.loads(line))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------- summary


@dataclass
class ScorecardSummary:
    """Aggregated grades over a journal window."""

    correct: int = 0
    correct_clears: int = 0
    correct_blocks: int = 0
    near_misses: int = 0
    violations: int = 0
    unmirrored_old: int = 0
    freed_bytes: int = 0
    trust_state: str = ""
    missing_test_incidents: list = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return (
            self.violations == 0
            and self.near_misses == 0
            and self.unmirrored_old == 0
        )


def summarize_events(events, trust_state: str = "", root_events=None) -> ScorecardSummary:
    """Aggregate journal event dicts into a ScorecardSummary.

    Graded (kind=scorecard) records drive the correctness counts;
    legacy cleared/reaped events contribute only ``freed_bytes`` (the
    footnote) so a graded clear is never double-counted. ``root_events``
    (raw events from the guardian's root cloud_dog.py audit log) are
    adapted through :func:`adapt_root_events` and merged as a
    first-class grading source.
    """
    s = ScorecardSummary(trust_state=trust_state)
    all_events = list(events or []) + adapt_root_events(root_events or [])
    for e in all_events:
        if isinstance(e, dict) and isinstance(e.get("_freed"), int):
            s.freed_bytes += e["_freed"]
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind") or "")
        if kind in ("cleared", "reaped"):
            size = e.get("size")
            if isinstance(size, int) and size > 0:
                s.freed_bytes += size
            continue
        if kind != SCORECARD_KIND:
            continue
        grade = str(e.get("grade") or "").upper()
        note = str(e.get("note") or "")
        if grade == CORRECT:
            s.correct += 1
            if "clear" in note.lower():
                s.correct_clears += 1
        elif grade == BLOCKED:
            s.correct_blocks += 1
        elif grade == NEAR_MISS:
            s.near_misses += 1
            if not e.get("regression_test"):
                s.missing_test_incidents.append(e)
        elif grade == VIOLATION:
            s.violations += 1
            if not e.get("regression_test"):
                s.missing_test_incidents.append(e)
        elif grade == UNMIRRORED_OLD:
            s.unmirrored_old += 1
    return s


def summarize_journal(journal_path: str, trust_path: str | None = None,
                      limit: int = 100000,
                      root_journal_path: str | None = None) -> ScorecardSummary:
    """Read the audit journal at ``journal_path`` and summarize it,
    attaching the current trust state. When ``root_journal_path`` is
    given, the guardian/root script's audit log is graded and merged
    as a first-class source too."""
    events: list[dict] = []
    try:
        from clouddog.audit import AuditJournal

        events = AuditJournal(journal_path).read(limit=limit)
    except Exception:  # noqa: BLE001 - a missing journal is an empty card
        events = []
    root_events = read_root_journal(root_journal_path, limit=limit) if root_journal_path else []
    state = ""
    try:
        state = _trust.current_state(trust_path)
    except Exception:  # noqa: BLE001
        state = _trust.REVOKED
    return summarize_events(events, trust_state=state, root_events=root_events)


def format_scorecard(summary: ScorecardSummary) -> str:
    """Human-readable scorecard. CORRECTNESS FIRST, bytes LAST — the
    ordering is the law, and the tests pin it."""
    lines = []
    if summary.unmirrored_old:
        lines.append(
            f"SYSTEM FAILURE — UNMIRRORED OLD WORK: {summary.unmirrored_old} "
            "file(s) sitting local-only past the mirror age with no store "
            "record (see UNMIRRORED_OLD journal records)"
        )
    lines.append(f"correct clears: {summary.correct_clears}")
    lines.append(
        f"correct actions (incl. stored-and-kept / kept-local): {summary.correct}"
    )
    lines.append(f"correct blocks: {summary.correct_blocks}")
    lines.append(f"near-misses: {summary.near_misses}")
    lines.append(f"violations: {summary.violations}")
    lines.append(f"unmirrored_old: {summary.unmirrored_old}")
    lines.append(f"trust_state: {summary.trust_state or _trust.REVOKED}")
    if summary.missing_test_incidents:
        lines.append(
            "incidents missing a linked regression test: "
            f"{len(summary.missing_test_incidents)}"
        )
        for e in summary.missing_test_incidents:
            lines.append(
                f"  MISSING-TEST {e.get('grade', '')} {e.get('path', '')} "
                f"({e.get('ts', '') or 'no ts'}) — link a regression test "
                "per docs/TRAINING-LOOP-SOP.md"
            )
    else:
        lines.append("incidents missing a linked regression test: 0")
    lines.append(f"freed_bytes (footnote): {summary.freed_bytes}")
    return "\n".join(lines)


def scorecard_line(summary: ScorecardSummary) -> str:
    """One-line correctness-first summary for enforce/heartbeat surfaces."""
    return (
        f"scorecard: correct_clears={summary.correct_clears} "
        f"correct_blocks={summary.correct_blocks} "
        f"near_misses={summary.near_misses} "
        f"violations={summary.violations} "
        f"unmirrored_old={summary.unmirrored_old} "
        f"trust_state={summary.trust_state or _trust.REVOKED} "
        f"freed_bytes={summary.freed_bytes}"
    )
