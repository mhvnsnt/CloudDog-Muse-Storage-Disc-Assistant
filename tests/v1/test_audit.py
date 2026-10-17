"""Tests for clouddog.audit — the append-only audit journal."""
import json
import os

from clouddog.audit import AuditEvent, AuditJournal, fmt_size


def make_events():
    return [
        AuditEvent(kind="stored", path="~/workspace/game/teardown/a.bin",
                   size=1_500_000, locator="mhvnsnt/repo@v1.0/a.bin",
                   note="upload verified byte-exact"),
        AuditEvent(kind="reaped", path="~/workspace/game/teardown/a.bin",
                   size=1_500_000, locator="mhvnsnt/repo@v1.0/a.bin",
                   note="hash match, deleted locally"),
        AuditEvent(kind="verified", path="~/workspace/game/teardown/b.bin",
                   size=600, locator="/terabox/CN-Gigaleak/show/b.bin",
                   note=""),
    ]


def test_record_appends_jsonl(tmp_path):
    j = AuditJournal(str(tmp_path / "audit.jsonl"))
    events = make_events()
    for e in events:
        j.record(e)
    with open(tmp_path / "audit.jsonl") as _fh:
        lines = _fh.read().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["kind"] == "stored"
    assert first["path"].endswith("a.bin")
    assert first["size"] == 1_500_000
    assert first["locator"] == "mhvnsnt/repo@v1.0/a.bin"


def test_record_fsyncs_and_md_append(tmp_path):
    jsonl = tmp_path / "audit.jsonl"
    md = tmp_path / "audit.md"
    j = AuditJournal(str(jsonl), str(md))
    for e in make_events():
        j.record(e)
    assert os.path.exists(jsonl)
    md_text = md.read_text()
    assert md_text.startswith("##")  # header written once on first record
    assert "a.bin" in md_text
    assert "b.bin" in md_text


def test_read_limit_returns_last_n(tmp_path):
    j = AuditJournal(str(tmp_path / "audit.jsonl"))
    for e in make_events():
        j.record(e)
    got = j.read(limit=2)
    assert len(got) == 2
    assert got[0]["kind"] == "reaped"
    assert got[1]["kind"] == "verified"
    assert j.read(limit=10) and len(j.read(limit=10)) == 3
    assert j.read(limit=0) == []


def test_read_missing_file_returns_empty(tmp_path):
    j = AuditJournal(str(tmp_path / "nope.jsonl"))
    assert j.read() == []


def test_render_markdown_matches_cleanup_log_style():
    md = AuditJournal.render_markdown(make_events(), "Sweep run")
    assert "## " in md
    assert "Sweep run" in md
    assert "- Reaped `~/workspace/game/teardown/a.bin`" in md
    assert "- Stored `~/workspace/game/teardown/a.bin`" in md
    assert "mhvnsnt/repo@v1.0/a.bin" in md
    assert "Total freed" in md
    # freed = 1_500_000 (reaped) only — stored/verified do not count
    assert "**Total freed:** 1.4M" in md


def test_render_markdown_header_format():
    md = AuditJournal.render_markdown(make_events(), "Cleanup")
    first = md.splitlines()[0]
    assert first.startswith("## ")
    assert " — Cleanup" in first


def test_render_markdown_empty_events():
    md = AuditJournal.render_markdown([], "Nothing")
    assert "## " in md
    assert "Total freed" not in md


def test_fmt_size():
    assert fmt_size(None) == "unknown size"
    assert fmt_size(512) == "512B"
    assert fmt_size(1_500_000) == "1.4M"
    assert fmt_size(1_073_741_824) == "1G"
