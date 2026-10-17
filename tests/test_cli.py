"""Tests for clouddog.cli. All store I/O goes through FakeStore; no network."""

from clouddog import cli
from clouddog.core import manifest
from tests.fake_store import scan_one, write_config


def _entries(env):
    return manifest.Journal(env["journal"]).entries()


def test_status_exits_0(env, capsys):
    assert cli.main(["status", "--config", env["config"]]) == 0
    out = capsys.readouterr().out
    assert "journal:" in out
    assert "stores:" in out
    assert "terabox: configured" in out


def test_init_writes_and_refuses_overwrite(tmp_path):
    target = tmp_path / "c.yaml"
    assert cli.main(["init", "--config", str(target)]) == 0
    assert target.exists()
    assert cli.main(["init", "--config", str(target)]) == 1
    assert cli.main(["init", "--config", str(target), "--force"]) == 0


def test_sweep_dry_run_plans_journals_and_uploads_nothing(env, fake, big_file, monkeypatch, capsys):
    scan_one(monkeypatch, big_file)
    assert cli.main(["sweep", "--config", env["config"]]) == 0
    out = capsys.readouterr().out
    assert "PLAN" in out
    assert "would mirror" in out
    assert fake.blobs == {}
    plans = [e for e in _entries(env) if e.action == "sweep-plan"]
    assert len(plans) == 1
    assert plans[0].dry_run is True


def test_sweep_below_threshold_takes_no_action_even_with_execute(
    tmp_path, fake, big_file, monkeypatch, capsys
):
    cfg = write_config(
        tmp_path,
        scan_roots=[str(tmp_path)],
        thresholds={"warn_pct": 100, "target_pct": 70, "ceiling_pct": 100},
    )
    scan_one(monkeypatch, big_file)
    assert cli.main(["sweep", "--config", cfg, "--execute", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "below" in out and "evaluation pass only" in out
    assert fake.blobs == {}


def test_sweep_execute_without_yes_exits_2(env, fake, big_file, monkeypatch, capsys):
    scan_one(monkeypatch, big_file)
    assert cli.main(["sweep", "--config", env["config"], "--execute"]) == 2
    assert "--execute --yes" in capsys.readouterr().err
    assert fake.blobs == {}


def test_sweep_execute_mirrors_but_never_deletes(env, fake, big_file, monkeypatch, tmp_path):
    # Force the "over warn threshold" path deterministically: the real disk
    # on this machine sits below the default 75% warn level.
    write_config(tmp_path, scan_roots=[str(tmp_path)], thresholds={"warn_pct": 0})
    scan_one(monkeypatch, big_file)
    assert cli.main(["sweep", "--config", env["config"], "--execute", "--yes"]) == 0
    assert len(fake.blobs) == 1
    assert big_file.exists()
    stored = [e for e in _entries(env) if e.action == "store"]
    assert len(stored) == 1
    assert stored[0].dry_run is False


def test_sweep_skips_in_progress_candidates(env, fake, monkeypatch, tmp_path, capsys):
    partial = tmp_path / "movie.mkv.part"
    partial.write_bytes(b"x" * 64)
    scan_one(monkeypatch, partial)
    assert cli.main(["sweep", "--config", env["config"]]) == 0
    assert "SKIP" in capsys.readouterr().out
    assert fake.blobs == {}


def test_store_dry_run_prints_plan_and_does_not_upload(env, fake, big_file, capsys):
    assert cli.main(["store", str(big_file), "--to", "terabox", "--config", env["config"]]) == 0
    out = capsys.readouterr().out
    assert "plan:" in out
    assert "terabox" in out
    assert fake.blobs == {}
    assert [e for e in _entries(env) if e.action == "store"] == []


def test_store_execute_without_yes_exits_2(env, fake, big_file):
    assert (
        cli.main(
            ["store", str(big_file), "--to", "terabox", "--execute", "--config", env["config"]]
        )
        == 2
    )
    assert fake.blobs == {}


def test_store_execute_with_yes_uploads_verifies_and_journals(env, fake, big_file):
    assert (
        cli.main(
            [
                "store",
                str(big_file),
                "--to",
                "terabox",
                "--execute",
                "--yes",
                "--remote-dir",
                "/CloudDog",
                "--remote-name",
                "copy.mkv",
                "--config",
                env["config"],
            ]
        )
        == 0
    )
    assert list(fake.blobs) == ["/CloudDog/copy.mkv"]
    stored = [e for e in _entries(env) if e.action == "store"]
    assert len(stored) == 1
    assert stored[0].dry_run is False
    assert stored[0].stores[0]["store"] == "fake"


def test_store_unknown_store_name_fails(env, fake, big_file):
    assert cli.main(["store", str(big_file), "--to", "nope", "--config", env["config"]]) == 1


def test_verify_command_reverifies_and_journals(env, fake, big_file, capsys):
    cli.main(
        ["store", str(big_file), "--to", "terabox", "--execute", "--yes", "--config", env["config"]]
    )
    assert cli.main(["verify", "--journal-entry", "1", "--config", env["config"]]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    verified = [e for e in _entries(env) if e.action == "verify"]
    assert len(verified) == 1
    assert verified[0].dry_run is False


def test_verify_out_of_range_fails(env):
    assert cli.main(["verify", "--journal-entry", "9", "--config", env["config"]]) == 1
