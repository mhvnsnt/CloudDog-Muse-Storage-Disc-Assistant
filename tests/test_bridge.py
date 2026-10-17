"""Tests for clouddog.bridge (registry -> v2 audit journal)."""

import json
import os

from clouddog import bridge


def _registry(tmp_path, entries):
    rp = tmp_path / "registry.json"
    rp.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return str(rp)


def test_event_maps_github_locators():
    event = bridge.registry_entry_to_audit_event(
        "/x/f.iso",
        {
            "local_path": "/x/f.iso",
            "store": "github",
            "repo": "o/r",
            "release": "t",
            "asset": "f.iso",
            "sha256": "abc",
            "size": 10,
            "status": "registered",
            "registered_at": "2026-10-17",
            "note": "",
        },
    )
    assert event["action"] == "registry-import"
    assert event["repo"] == "o/r"
    assert event["release"] == "t"
    assert event["asset"] == "f.iso"
    assert event["imported_by"] == "clouddog.bridge"
    assert event["ts"]  # UTC ISO timestamp present


def test_event_maps_terabox_and_kaggle_locators():
    tb = bridge.registry_entry_to_audit_event(
        "/x/g.bin", {"store": "terabox", "remote_dir": "/CN-Gigaleak", "asset": "g.bin"}
    )
    assert tb["remote_dir"] == "/CN-Gigaleak"
    assert tb["asset"] == "g.bin"
    kg = bridge.registry_entry_to_audit_event("/x/h.bin", {"store": "kaggle", "dataset": "user/ds"})
    assert kg["dataset"] == "user/ds"


def test_import_appends_audit_events(tmp_path):
    rp = _registry(
        tmp_path,
        {
            "/x/f.iso": {
                "local_path": "/x/f.iso",
                "store": "github",
                "repo": "o/r",
                "release": "t",
                "asset": "f.iso",
                "sha256": "abc",
                "size": 10,
                "status": "registered",
            },
            "/x/bad": "not-a-dict",
        },
    )
    ap = str(tmp_path / "audit.jsonl")
    assert bridge.import_registry_to_audit(rp, ap) == 1
    with open(ap, encoding="utf-8") as fh:
        lines = fh.read().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["action"] == "registry-import"
    assert event["local_path"] == "/x/f.iso"
    # registry file itself untouched
    with open(rp, encoding="utf-8") as fh:
        assert len(json.loads(fh.read())["entries"]) == 2


def test_import_missing_registry_returns_zero(tmp_path):
    ap = str(tmp_path / "audit.jsonl")
    assert bridge.import_registry_to_audit(str(tmp_path / "nope.json"), ap) == 0
    assert not os.path.exists(ap)


def test_import_corrupt_registry_returns_zero(tmp_path):
    rp = tmp_path / "registry.json"
    rp.write_text("{not json", encoding="utf-8")
    assert bridge.import_registry_to_audit(str(rp), str(tmp_path / "a.jsonl")) == 0
