"""Tests for clouddog.core.monitor (sibling-owned)."""

from clouddog.core import monitor


def test_disk_usage_returns_sane_numbers(tmp_path):
    ds = monitor.disk_usage(str(tmp_path))
    assert ds.path == str(tmp_path)
    assert ds.total_bytes > 0
    assert ds.used_bytes >= 0
    assert ds.free_bytes >= 0
    assert 0.0 <= ds.pct_used <= 100.0
