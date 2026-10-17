"""Tests for clouddog.core.manifest (sibling-owned)."""

import json

from clouddog.core import manifest


def _entry(**overrides):
    data = {
        "ts": "2026-10-17T12:00:00+00:00",
        "action": "store",
        "path": "/tmp/x/movie.mkv",
        "size_bytes": 4096,
        "sha256": "abc123def456",
        "stores": [{"store": "fake", "uri": "fake:///CloudDog/movie.mkv"}],
        "approved_by": "test",
        "dry_run": False,
        "note": "test note",
    }
    data.update(overrides)
    return manifest.JournalEntry(**data)


def test_append_entries_roundtrip(tmp_path):
    journal = manifest.Journal(str(tmp_path / "j.jsonl"))
    journal.append(_entry())
    journal.append(_entry(action="verify", note="FAILED"))
    entries = journal.entries()
    assert len(entries) == 2
    assert entries[0] == _entry()
    assert entries[1].action == "verify"
    assert entries[1].note == "FAILED"


def test_render_markdown_contains_path_and_sha(tmp_path):
    journal = manifest.Journal(str(tmp_path / "j.jsonl"))
    rendered = journal.render_markdown(_entry())
    assert "/tmp/x/movie.mkv" in rendered
    assert "abc123def456" in rendered


def test_empty_journal_returns_no_entries(tmp_path):
    journal = manifest.Journal(str(tmp_path / "j.jsonl"))
    assert journal.entries() == []


def test_import_registry(tmp_path):
    registry = {
        "version": 1,
        "entries": {
            "/x/f.iso": {
                "local_path": "/x/f.iso",
                "store": "github",
                "repo": "o/r",
                "release": "t",
                "asset": "f.iso",
                "sha256": "abc",
                "size": 123,
                "note": "",
                "registered_at": "2026-10-17 12:00 CDT",
                "status": "registered",
            }
        },
    }
    reg_path = tmp_path / "registry.json"
    reg_path.write_text(json.dumps(registry), encoding="utf-8")
    journal = manifest.Journal(str(tmp_path / "j.jsonl"))
    assert journal.import_registry(str(reg_path)) == 1
    entries = journal.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == "store"
    assert entry.approved_by == "cloud_dog.py"
    assert entry.path == "/x/f.iso"
    assert entry.size_bytes == 123
    assert entry.sha256 == "abc"
    assert entry.dry_run is False
    assert "github.com/o/r/releases/download/t/f.iso" in entry.stores[0]["uri"]


def test_import_registry_missing_file_returns_zero(tmp_path):
    journal = manifest.Journal(str(tmp_path / "j.jsonl"))
    assert journal.import_registry(str(tmp_path / "nope.json")) == 0
    assert journal.entries() == []
