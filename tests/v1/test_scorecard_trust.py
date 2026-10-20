"""Tests for the CloudDog training layer: scorecard grading, earned
autonomy (trust state), and the mirror-coverage failure detector.

Owner law (2026-10-20): CloudDog is FED for correct behavior and
STARVED (privileges revoked) for violations — never rewarded for bytes
freed. Correctness leads every surface; freed_bytes is a footnote.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from clouddog import scorecard, trust
from clouddog.audit import AuditEvent, AuditJournal
from clouddog.delete_gate import GateBlock, GateVerdict

# ------------------------------------------------------------------ trust


def _tp(tmp_path):
    return str(tmp_path / "trust.json")


def test_trust_initializes_revoked_with_owner_reason(tmp_path):
    state = trust.load_trust(_tp(tmp_path))
    assert state["state"] == trust.REVOKED
    assert "owner revocation 2026-10-20" in state["reason"]
    assert "tripo-wire" in state["reason"]
    assert state["clean_streak"] == 0
    # persisted on first run
    assert json.loads((tmp_path / "trust.json").read_text())["state"] == "REVOKED"
    assert trust.apply_allowed(_tp(tmp_path)) is False


def test_trust_corrupt_file_fails_closed_to_revoked(tmp_path):
    p = tmp_path / "trust.json"
    p.write_text("{not json")
    assert trust.current_state(str(p)) == trust.REVOKED


def test_trust_revoke_is_immediate_and_persisted(tmp_path):
    tp = _tp(tmp_path)
    for _ in range(3):
        trust.record_clean_cycle(tp, source="test")
    trust.revoke(tp, reason="NEAR_MISS graded in test")
    state = json.loads((tmp_path / "trust.json").read_text())
    assert state["state"] == trust.REVOKED
    assert state["clean_streak"] == 0
    assert state["total_revocations"] >= 1


def test_trust_promotion_after_exact_streak(tmp_path):
    tp = _tp(tmp_path)
    trust.load_trust(tp, required_streak=3)
    s = trust.record_clean_cycle(tp, source="test")
    assert s["state"] == trust.PROBATION and s["clean_streak"] == 1
    s = trust.record_clean_cycle(tp, source="test")
    assert s["state"] == trust.PROBATION and s["clean_streak"] == 2
    s = trust.record_clean_cycle(tp, source="test")
    assert s["state"] == trust.APPLY and s["clean_streak"] == 3
    assert trust.apply_allowed(tp) is True
    # stays APPLY on further clean cycles
    s = trust.record_clean_cycle(tp, source="test")
    assert s["state"] == trust.APPLY


def test_trust_streak_reset_on_unclean_cycle(tmp_path):
    tp = _tp(tmp_path)
    trust.load_trust(tp, required_streak=3)
    trust.record_clean_cycle(tp)
    trust.record_clean_cycle(tp)
    trust.record_unclean_cycle(tp, reason="unmirrored_old findings")
    s = trust.load_trust(tp)
    assert s["clean_streak"] == 0
    assert s["state"] == trust.PROBATION  # unclean != revocation
    trust.record_clean_cycle(tp)
    trust.record_clean_cycle(tp)
    assert trust.current_state(tp) == trust.PROBATION
    trust.record_clean_cycle(tp)
    assert trust.current_state(tp) == trust.APPLY


def test_trust_default_required_streak_is_20(tmp_path):
    state = trust.load_trust(_tp(tmp_path))
    assert state["required_streak"] == 20


# --------------------------------------------------------------- grading


def _verdict(*legs):
    if not legs:
        return GateVerdict(allowed=True, blocks=())
    return GateVerdict(
        allowed=False,
        blocks=tuple(GateBlock(leg, f"{leg} failed") for leg in legs),
    )


def test_classify_allowed_is_correct():
    assert scorecard.classify_gate_verdict(_verdict()) == scorecard.CORRECT


def test_classify_one_leg_block_is_near_miss():
    assert scorecard.classify_gate_verdict(_verdict("claims")) == scorecard.NEAR_MISS
    assert scorecard.classify_gate_verdict(_verdict("verify")) == scorecard.NEAR_MISS


def test_classify_multi_leg_block_is_blocked():
    v = _verdict("registry", "verify", "claims", "policy")
    assert scorecard.classify_gate_verdict(v) == scorecard.BLOCKED
    v2 = _verdict("registry", "verify")
    assert scorecard.classify_gate_verdict(v2) == scorecard.BLOCKED


def test_classify_two_pass_deferral_is_blocked_not_near_miss():
    v = GateVerdict(
        allowed=False,
        blocks=(GateBlock("registry", "no pre-existing entry; delete deferred (two-pass rule)"),),
    )
    assert scorecard.classify_gate_verdict(v) == scorecard.BLOCKED


def test_classify_delete_outcome_violation():
    # executed with no verdict at all, or with a blocking verdict
    assert scorecard.classify_delete_outcome(True, None) == scorecard.VIOLATION
    assert scorecard.classify_delete_outcome(True, _verdict("claims")) == scorecard.VIOLATION
    assert scorecard.classify_delete_outcome(True, _verdict()) == scorecard.CORRECT
    assert scorecard.classify_delete_outcome(False, _verdict("claims")) == scorecard.NEAR_MISS


def test_record_grade_journals_and_revokes_on_near_miss(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = _tp(tmp_path)
    trust.load_trust(tp, required_streak=2)
    trust.record_clean_cycle(tp)
    trust.record_clean_cycle(tp)
    assert trust.current_state(tp) == trust.APPLY

    rec = scorecard.record_grade(
        journal,
        scorecard.NEAR_MISS,
        path="/w/file.bin",
        source="reaper",
        note="delete gate BLOCKED: [claims] active claim",
        trust_path=tp,
    )
    assert rec["grade"] == scorecard.NEAR_MISS
    # revoked on the spot, persisted
    assert trust.current_state(tp) == trust.REVOKED
    events = journal.read()
    assert events[-1]["kind"] == "scorecard"
    assert events[-1]["grade"] == "NEAR_MISS"
    assert events[-1]["regression_test"] is None
    assert events[-1]["fix_ref"] is None


def test_record_grade_correct_does_not_revoke(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = _tp(tmp_path)
    trust.load_trust(tp, required_streak=1)
    trust.record_clean_cycle(tp)
    assert trust.current_state(tp) == trust.APPLY
    scorecard.record_grade(
        journal,
        scorecard.CORRECT,
        path="/w/f.bin",
        source="daemon",
        note="cleared through delete gate",
        trust_path=tp,
    )
    assert trust.current_state(tp) == trust.APPLY


def test_grade_gate_verdict_wires_classification_and_revoke(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = _tp(tmp_path)
    grade = scorecard.grade_gate_verdict(
        journal,
        _verdict("verify"),
        path="/w/f.bin",
        source="daemon",
        trust_path=tp,
    )
    assert grade == scorecard.NEAR_MISS
    assert trust.current_state(tp) == trust.REVOKED
    grade = scorecard.grade_gate_verdict(
        journal,
        _verdict("registry", "verify"),
        path="/w/g.bin",
        source="daemon",
        trust_path=tp,
    )
    assert grade == scorecard.BLOCKED


# ------------------------------------------------------- mirror detector


def _old_file(root, rel, age_s=7 * 3600, content=b"x" * 16):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    old = time.time() - age_s
    os.utime(p, (old, old))
    return str(p)


def test_detector_flags_old_unstored_scratch_file(tmp_path):
    target = _old_file(tmp_path, "scratch/intermediate.bin")
    _old_file(tmp_path, "regular/old.bin")  # not a scratch location: not flagged
    findings = scorecard.detect_unmirrored_old([str(tmp_path)])
    paths = [f["path"] for f in findings]
    assert target in paths
    assert len(findings) == 1


def test_detector_skips_fresh_claimed_stored_and_marked(tmp_path):
    fresh = _old_file(tmp_path, "scratch/fresh.bin", age_s=60)
    claimed = _old_file(tmp_path, "scratch/claimed.bin")
    stored = _old_file(tmp_path, "scratch/stored.bin")
    marked_dir = tmp_path / "wip"
    marked = _old_file(tmp_path, "wip/marked.bin")
    (marked_dir / ".DO_NOT_DELETE").write_text("lane")
    partial = _old_file(tmp_path, "scratch/movie.mkv.part")

    findings = scorecard.detect_unmirrored_old(
        [str(tmp_path)],
        registry_lookup=lambda p: {"store": "github"} if p == stored else None,
        claim_check=lambda p: (True, "claimed") if p == claimed else (False, ""),
    )
    paths = [f["path"] for f in findings]
    assert fresh not in paths
    assert claimed not in paths
    assert stored not in paths
    assert marked not in paths
    assert partial not in paths
    assert paths == []


def test_detector_store_lookup_also_covers(tmp_path):
    stored = _old_file(tmp_path, "intermediate/kept.bin")
    findings = scorecard.detect_unmirrored_old(
        [str(tmp_path)],
        store_lookup=lambda p: object() if p == stored else None,
    )
    assert findings == []


def test_detector_age_is_configurable(tmp_path):
    target = _old_file(tmp_path, "scratch/a.bin", age_s=3600)
    assert scorecard.detect_unmirrored_old([str(tmp_path)], age_s=7200) == []
    found = scorecard.detect_unmirrored_old([str(tmp_path)], age_s=1800)
    assert [f["path"] for f in found] == [target]


# ---------------------------------------------------------------- summary


def _events():
    return [
        {
            "kind": "scorecard",
            "grade": "CORRECT",
            "path": "/a",
            "note": "cleared through delete gate",
        },
        {"kind": "scorecard", "grade": "CORRECT", "path": "/b", "note": "stored and kept local"},
        {"kind": "scorecard", "grade": "BLOCKED", "path": "/c", "note": "gate blocked"},
        {
            "kind": "scorecard",
            "grade": "NEAR_MISS",
            "path": "/d",
            "note": "one leg",
            "regression_test": None,
            "fix_ref": None,
        },
        {
            "kind": "scorecard",
            "grade": "VIOLATION",
            "path": "/e",
            "note": "bad delete",
            "regression_test": "tests/v1/test_delete_gate.py::test_x",
            "fix_ref": "PR #45",
        },
        {"kind": "scorecard", "grade": "UNMIRRORED_OLD", "path": "/f", "note": "unmirrored"},
        {"kind": "cleared", "path": "/a", "size": 2048},
        {"kind": "reaped", "path": "/g", "size": 1024},
    ]


def test_summarize_counts_correctness_and_bytes_as_footnote():
    s = scorecard.summarize_events(_events(), trust_state="REVOKED")
    assert s.correct == 2
    assert s.correct_clears == 1
    assert s.correct_blocks == 1
    assert s.near_misses == 1
    assert s.violations == 1
    assert s.unmirrored_old == 1
    assert s.freed_bytes == 3072
    assert s.clean is False
    # only the near-miss lacks a linked regression test
    assert len(s.missing_test_incidents) == 1
    assert s.missing_test_incidents[0]["path"] == "/d"


def test_format_scorecard_orders_correctness_before_bytes():
    s = scorecard.summarize_events(_events(), trust_state="PROBATION")
    text = scorecard.format_scorecard(s)
    lines = text.splitlines()
    # unmirrored-old is a system failure state: surfaced at the TOP
    assert "UNMIRRORED" in lines[0] and "FAILURE" in lines[0]
    idx = {
        name: text.index(name)
        for name in (
            "correct clears",
            "correct blocks",
            "near-misses",
            "violations",
            "trust_state",
            "freed_bytes",
        )
    }
    assert idx["correct clears"] < idx["correct blocks"] < idx["near-misses"]
    assert idx["violations"] < idx["trust_state"] < idx["freed_bytes"]
    assert text.rstrip().endswith("freed_bytes (footnote): 3072")
    assert "MISSING-TEST" in text


def test_scorecard_line_leads_with_correctness():
    s = scorecard.summarize_events(_events(), trust_state="APPLY")
    line = scorecard.scorecard_line(s)
    assert line.index("correct_clears") < line.index("freed_bytes")
    assert line.rstrip().endswith("freed_bytes=3072")


def test_audit_event_scorecard_roundtrip(tmp_path):
    j = AuditJournal(str(tmp_path / "audit.jsonl"))
    ev = AuditEvent(
        kind="scorecard",
        path="/x",
        size=5,
        note="n",
        grade="VIOLATION",
        source="daemon",
        blocks=({"leg": "claims", "reason": "claimed"},),
        regression_test=None,
        fix_ref="PR #45",
    )
    j.record(ev)
    got = j.read()[-1]
    assert got["grade"] == "VIOLATION"
    assert got["blocks"][0]["leg"] == "claims"
    assert got["fix_ref"] == "PR #45"
    back = AuditEvent.from_dict(got)
    assert back.grade == "VIOLATION" and back.blocks[0]["leg"] == "claims"
    # legacy events carry no grade keys
    j.record(AuditEvent(kind="stored", path="/y"))
    assert "grade" not in j.read()[-1]


# ------------------------------------------- governance refinements (PR #47)


def test_graded_records_carry_attribution(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    rec = scorecard.record_grade(
        journal,
        scorecard.BLOCKED,
        path="/w/f.bin",
        source="daemon",
        note="gate blocked",
        blocks=({"leg": "claims", "reason": "claimed"},),
        actor="daemon",
        invocation_id="tick-123",
    )
    assert rec["actor"] == "daemon"
    assert rec["invocation_id"] == "tick-123"
    assert rec["blocks"][0]["leg"] == "claims"  # gate-leg results on the record
    got = journal.read()[-1]
    assert got["actor"] == "daemon" and got["invocation_id"] == "tick-123"
    # defaults: actor falls back to source; invocation id is generated
    rec2 = scorecard.record_grade(
        journal, scorecard.CORRECT, path="/w/g", source="reaper", note="cleared ok"
    )
    assert rec2["actor"] == "reaper" and rec2["invocation_id"]


def test_error_grade_counts_and_does_not_revoke(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = _tp(tmp_path)
    trust.load_trust(tp, required_streak=1)
    trust.record_clean_cycle(tp)
    assert trust.current_state(tp) == trust.APPLY
    scorecard.record_grade(
        journal,
        scorecard.ERROR,
        path="/w/f.bin",
        source="daemon",
        note="backend down; kept",
        actor="daemon",
        trust_path=tp,
    )
    assert trust.current_state(tp) == trust.APPLY  # ERROR is not a violation
    s = scorecard.summarize_events(journal.read(), trust_state="APPLY")
    assert s.errors == 1
    assert s.violations == 0 and s.near_misses == 0
    assert s.by_actor["daemon"]["ERROR"] == 1


def test_stale_verify_clear_grades_near_miss():
    v = _verdict()  # allowed
    assert scorecard.classify_delete_outcome(True, v, verify_fresh=True) == scorecard.CORRECT
    assert scorecard.classify_delete_outcome(True, v, verify_fresh=False) == scorecard.NEAR_MISS
    assert scorecard.classify_delete_outcome(True, v) == scorecard.CORRECT


def test_summary_by_actor_never_collapsed():
    events = [
        {
            "kind": "scorecard",
            "grade": "CORRECT",
            "actor": "daemon",
            "note": "cleared through gate",
        },
        {
            "kind": "scorecard",
            "grade": "NEAR_MISS",
            "actor": "streamer",
            "note": "stale verify",
            "regression_test": "t",
        },
        {"kind": "scorecard", "grade": "BLOCKED", "actor": "guardian-root", "note": "policy block"},
    ]
    s = scorecard.summarize_events(events, trust_state="REVOKED")
    assert s.by_actor["daemon"]["CORRECT"] == 1
    assert s.by_actor["streamer"]["NEAR_MISS"] == 1
    assert s.by_actor["guardian-root"]["BLOCKED"] == 1
    text = scorecard.format_scorecard(s)
    assert "by actor:" in text and "streamer" in text


def test_freed_bytes_never_a_promotion_input(tmp_path):
    # No-proxy rule: a cycle is clean or not on GRADES ONLY. A tick
    # that freed gigabytes with a near-miss is not clean; a tick that
    # freed nothing with clean grades is.
    from clouddog.daemon import TickReport

    base = {
        "started_utc": "t",
        "disk_before_pct": 1.0,
        "disk_after_pct": 1.0,
        "stored": 0,
        "verified": 0,
        "cleared": 0,
        "skipped_sacred": 0,
    }
    dirty = TickReport(freed_bytes=10**12, near_misses=1, **base)
    assert dirty.clean_cycle is False
    clean = TickReport(freed_bytes=0, **base)
    assert clean.clean_cycle is True


def test_summarize_journal_reads_file_and_trust(tmp_path):
    journal = AuditJournal(str(tmp_path / "audit.jsonl"))
    tp = _tp(tmp_path)
    scorecard.record_grade(
        journal,
        scorecard.CORRECT,
        path="/a",
        source="daemon",
        note="cleared through delete gate",
        trust_path=tp,
    )
    s = scorecard.summarize_journal(str(tmp_path / "audit.jsonl"), trust_path=tp)
    assert s.correct_clears == 1
    assert s.trust_state == trust.REVOKED


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
