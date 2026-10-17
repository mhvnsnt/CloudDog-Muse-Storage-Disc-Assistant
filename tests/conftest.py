"""Shared fixtures for the CloudDog test suite."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from clouddog import stores as stores_mod
from clouddog.policy import laws
from tests.fake_store import FakeStore, write_config


@pytest.fixture(autouse=True)
def no_tmp_dir_hint(monkeypatch):
    """The sibling laws treat any path under a "tmp" dir as in-flight.

    Pytest's tmp_path always lives under /tmp, so drop that one hint for
    the suite; the in-progress *suffix* checks under test are unaffected.
    """
    monkeypatch.setattr(laws, "IN_PROGRESS_DIR_HINTS", {"staging", "downloads", "incomplete"})


@pytest.fixture
def env(tmp_path):
    """A test environment: config file, journal path, and scan root in tmp."""
    cfg = write_config(tmp_path, scan_roots=[str(tmp_path)])
    return {
        "config": cfg,
        "journal": str(tmp_path / "journal.jsonl"),
        "root": str(tmp_path),
    }


@pytest.fixture
def fake(monkeypatch):
    """A FakeStore installed as the return of stores.get_store."""
    store = FakeStore({})
    monkeypatch.setattr(stores_mod, "get_store", lambda name, cfg: store)
    return store


@pytest.fixture
def big_file(tmp_path):
    """A small stand-in for a large media file."""
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"x" * 4096)
    return target
