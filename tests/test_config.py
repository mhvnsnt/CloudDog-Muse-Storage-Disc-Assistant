"""Tests for clouddog.config (sibling-owned)."""

import pytest

from clouddog import config as config_mod


def test_defaults():
    cfg = config_mod.default_config()
    assert cfg.thresholds == {"warn_pct": 75, "target_pct": 70, "ceiling_pct": 88}
    assert cfg.journal_path == "~/.clouddog/journal.jsonl"
    assert isinstance(cfg.stores, dict)
    assert isinstance(cfg.scan_roots, list)


def test_invalid_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("stores: [unclosed\n\t: bad indent\n", encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(str(bad))


def test_non_mapping_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(str(bad))


def test_missing_file_returns_defaults():
    cfg = config_mod.load_config("/nonexistent/path/clouddog.yaml")
    assert cfg.thresholds["warn_pct"] == 75


def test_write_default_then_load(tmp_path):
    target = tmp_path / "clouddog.yaml"
    config_mod.write_default_config(str(target))
    assert target.exists()
    cfg = config_mod.load_config(str(target))
    assert cfg.thresholds["warn_pct"] == 75
    assert cfg.scan_roots
