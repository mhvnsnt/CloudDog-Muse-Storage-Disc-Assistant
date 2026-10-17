"""Tests for clouddog.daemon. No network; FakeStore only."""

from clouddog import config as config_mod
from clouddog import daemon as daemon_mod
from clouddog import stores as stores_mod
from clouddog.core import manifest
from tests.fake_store import scan_one


def _entries(env):
    return manifest.Journal(env["journal"]).entries()


def test_run_once_returns_expected_keys_and_mirrors(env, fake, big_file, monkeypatch):
    scan_one(monkeypatch, big_file)
    cfg = config_mod.load_config(env["config"])
    report = daemon_mod.run_once(cfg)
    assert set(report) >= {
        "ts",
        "disk",
        "candidates",
        "mirrored",
        "journal_entries",
        "notes",
    }
    assert report["candidates"] == 1
    assert report["mirrored"] == [str(big_file)]
    assert report["journal_entries"] >= 1
    assert isinstance(report["disk"], dict)
    assert big_file.exists()
    assert len(fake.blobs) == 1


def test_run_once_never_deletes_local_files(env, fake, big_file, monkeypatch):
    scan_one(monkeypatch, big_file)
    cfg = config_mod.load_config(env["config"])
    daemon_mod.run_once(cfg)
    assert big_file.exists()
    assert big_file.read_bytes() == b"x" * 4096


def test_run_once_skips_already_mirrored(env, fake, big_file, monkeypatch):
    scan_one(monkeypatch, big_file)
    cfg = config_mod.load_config(env["config"])
    first = daemon_mod.run_once(cfg)
    assert first["mirrored"] == [str(big_file)]
    second = daemon_mod.run_once(cfg)
    assert second["mirrored"] == []
    assert any("already mirrored" in note for note in second["notes"])
    assert len(fake.blobs) == 1


def test_run_once_skips_in_progress(env, fake, monkeypatch, tmp_path):
    partial = tmp_path / "movie.mkv.part"
    partial.write_bytes(b"x" * 64)
    scan_one(monkeypatch, partial)
    cfg = config_mod.load_config(env["config"])
    report = daemon_mod.run_once(cfg)
    assert report["mirrored"] == []
    assert any("in-progress" in note for note in report["notes"])
    assert fake.blobs == {}
    assert partial.exists()


def test_run_once_notes_store_failure(env, monkeypatch, big_file):
    scan_one(monkeypatch, big_file)

    def boom(name, cfg):
        raise stores_mod.StoreUnavailable("no credentials")

    monkeypatch.setattr(stores_mod, "get_store", boom)
    cfg = config_mod.load_config(env["config"])
    report = daemon_mod.run_once(cfg)
    assert report["mirrored"] == []
    assert any("failed" in note for note in report["notes"])
    assert big_file.exists()


def test_run_loop_once_prints_one_line_report(env, fake, big_file, monkeypatch, capsys):
    scan_one(monkeypatch, big_file)
    cfg = config_mod.load_config(env["config"])
    report = daemon_mod.run_loop(cfg, once=True)
    assert report is not None
    assert report["candidates"] == 1
    out = capsys.readouterr().out
    assert "mirrored=1" in out
    assert "candidates=1" in out
