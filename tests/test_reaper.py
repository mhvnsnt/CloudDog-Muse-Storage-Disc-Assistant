"""Tests for clouddog.reaper — the DELETE-AS-YOU-GO verified-delete engine."""
import hashlib
import json
import os

import pytest

from clouddog.reaper import Reaper, ReapReport, compute_hash


def git_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@pytest.fixture()
def lane(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    log_path = str(tmp_path / "reaper.log")
    state_path = str(tmp_path / "reaper_state.json")
    return src, log_path, state_path


def write(src, rel, data: bytes):
    p = src / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def make_reaper(src, manifest, log_path, state_path, **kw):
    # Live-delete tests simulate a verified store; the store_verify wiring
    # itself is covered by test_reaper_refuses_without_store_verify.
    kw.setdefault("store_verify", lambda rel, full: True)
    return Reaper(str(src), manifest, log_path, state_path=state_path, **kw)


def test_dry_run_deletes_nothing_but_logs_would_reap(lane):
    src, log_path, state_path = lane
    write(src, "a.bin", b"one")
    write(src, "b.bin", b"two")
    manifest = {"a.bin": git_sha(b"one"), "b.bin": git_sha(b"two")}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=True)
    assert (src / "a.bin").exists()
    assert (src / "b.bin").exists()
    assert report.reaped == 0
    assert report.freed_bytes == 0
    log = open(log_path).read()
    assert log.count("WOULD-REAP") == 2
    assert "REAPED a.bin" not in log


def test_live_reap_deletes_verified_files(lane):
    src, log_path, state_path = lane
    data1, data2 = b"hello" * 100, b"world" * 200
    write(src, "a.bin", data1)
    write(src, "sub/b.bin", data2)
    manifest = {"a.bin": git_sha(data1), "sub/b.bin": git_sha(data2)}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.reaped == 2
    assert report.mismatches == 0
    assert report.halted is False
    assert not (src / "a.bin").exists()
    assert not (src / "sub/b.bin").exists()
    assert report.freed_bytes == len(data1) + len(data2)
    log = open(log_path).read()
    assert "REAPED a.bin" in log
    assert "REAPED sub/b.bin" in log


def test_mismatch_keeps_file(lane):
    src, log_path, state_path = lane
    write(src, "a.bin", b"changed!")
    manifest = {"a.bin": git_sha(b"original")}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.mismatches == 1
    assert report.reaped == 0
    assert (src / "a.bin").exists()  # NEVER deleted on mismatch
    assert "MISMATCH a.bin" in open(log_path).read()


def test_guard_skips_traversal_and_absolute_paths(lane):
    src, log_path, state_path = lane
    # file OUTSIDE src_dir that must never be touched
    evil = src.parent / "evil.txt"
    evil.write_bytes(b"evil")
    outside_abs = str(src.parent / "abs_evil.txt")
    open(outside_abs, "w").write("abs evil")
    manifest = {
        "../evil.txt": git_sha(b"evil"),
        outside_abs: git_sha(b"abs evil"),
    }
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.skipped == 2
    assert report.reaped == 0
    assert evil.exists()
    assert os.path.exists(outside_abs)
    assert "GUARD-SKIP" in open(log_path).read()


def test_guard_skips_protected_suffix(lane):
    src, log_path, state_path = lane
    write(src, "notes.log", b"do not delete me")
    write(src, "script.py", b"print(1)")
    manifest = {
        "notes.log": git_sha(b"do not delete me"),
        "script.py": git_sha(b"print(1)"),
    }
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.skipped == 2
    assert report.reaped == 0
    assert (src / "notes.log").exists()
    assert (src / "script.py").exists()


def test_guard_skips_log_and_state_basenames(lane):
    src, log_path, state_path = lane
    # even if a file inside src_dir shares the log/state basename, keep it
    same = write(src, os.path.basename(log_path), b"log content")
    manifest = {os.path.basename(log_path): git_sha(b"log content")}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.skipped == 1
    assert same.exists()


def test_symlink_escape_not_deleted(lane):
    src, log_path, state_path = lane
    target = src.parent / "target.txt"
    target.write_bytes(b"target data")
    (src / "link.bin").symlink_to(target)
    manifest = {"link.bin": git_sha(b"target data")}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.skipped == 1
    assert report.reaped == 0
    assert target.exists()  # outside src_dir, never touched


def test_spot_check_failure_halts_and_preserves_rest(lane):
    src, log_path, state_path = lane
    data1, data2 = b"first", b"second"
    write(src, "a.bin", data1)
    write(src, "b.bin", data2)
    manifest = {"a.bin": git_sha(data1), "b.bin": git_sha(data2)}
    calls = []

    def spot_check(rel, sha):
        calls.append(rel)
        return False

    r = make_reaper(src, manifest, log_path, state_path,
                    spot_check=spot_check, spot_every=1)
    report = r.reap_once(dry_run=False)
    assert report.halted is True
    assert report.halt_reason
    assert calls == ["a.bin"]
    assert not (src / "a.bin").exists()
    assert (src / "b.bin").exists()  # subsequent entries untouched
    # state was saved: a.bin is marked reaped
    state = json.loads(open(state_path).read())
    assert state["reaped"] == ["a.bin"]
    # second instance does not re-reap a.bin; b.bin reaps cleanly now that
    # spot checks pass
    r2 = make_reaper(src, manifest, log_path, state_path,
                     spot_check=lambda rel, sha: True, spot_every=1)
    report2 = r2.reap_once(dry_run=False)
    assert report2.halted is False
    assert report2.skipped == 1  # a.bin, from saved state
    assert report2.reaped == 1   # b.bin
    assert not (src / "b.bin").exists()


def test_state_persistence_second_instance_does_not_rereap(lane):
    src, log_path, state_path = lane
    data = b"gone soon"
    write(src, "a.bin", data)
    manifest = {"a.bin": git_sha(data)}
    r = make_reaper(src, manifest, log_path, state_path)
    report1 = r.reap_once(dry_run=False)
    assert report1.reaped == 1
    state = json.loads(open(state_path).read())
    assert state["reaped"] == ["a.bin"]
    r2 = make_reaper(src, manifest, log_path, state_path)
    report2 = r2.reap_once(dry_run=False)
    assert report2.reaped == 0
    assert report2.skipped == 1


def test_state_written_atomically(lane):
    src, log_path, state_path = lane
    write(src, "a.bin", b"x")
    manifest = {"a.bin": git_sha(b"x")}
    r = make_reaper(src, manifest, log_path, state_path)
    r.reap_once(dry_run=False)
    assert not os.path.exists(state_path + ".tmp")
    json.loads(open(state_path).read())  # valid JSON


def test_sha256_algo(lane):
    src, log_path, state_path = lane
    data = b"release asset bytes"
    write(src, "asset.zip", data)
    manifest = {"asset.zip": hashlib.sha256(data).hexdigest()}
    r = make_reaper(src, manifest, log_path, state_path, hash_algo="sha256")
    report = r.reap_once(dry_run=False)
    assert report.reaped == 1
    assert not (src / "asset.zip").exists()


def test_unknown_algo_rejected(lane):
    src, log_path, state_path = lane
    with pytest.raises(ValueError):
        Reaper(str(src), {}, log_path, state_path=state_path, hash_algo="md5")


def test_empty_parents_pruned(lane):
    src, log_path, state_path = lane
    data = b"nested"
    write(src, "deep/nest/a.bin", data)
    manifest = {"deep/nest/a.bin": git_sha(data)}
    r = make_reaper(src, manifest, log_path, state_path)
    r.reap_once(dry_run=False)
    assert not (src / "deep" / "nest").exists()
    assert src.exists()


def test_missing_file_marked_skipped(lane):
    src, log_path, state_path = lane
    manifest = {"gone.bin": git_sha(b"whatever")}
    r = make_reaper(src, manifest, log_path, state_path)
    report = r.reap_once(dry_run=False)
    assert report.skipped == 1
    assert report.reaped == 0
    state = json.loads(open(state_path).read())
    assert "gone.bin" in state["reaped"]  # never retried


def test_compute_hash_git_sha1():
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"abc")
        path = f.name
    assert compute_hash(path, "git-sha1") == git_sha(b"abc")
    assert compute_hash(path, "sha256") == hashlib.sha256(b"abc").hexdigest()


def test_reaper_refuses_without_store_verify(lane):
    """C1: fail closed — no live store check wired means no deletions."""
    from clouddog.reaper import compute_hash
    src, log_path, state_path = lane
    write(src, "a.txt", b"hello")
    manifest = {"a.txt": compute_hash(str(src / "a.txt"))}
    r = Reaper(str(src), manifest, log_path, state_path=state_path,
               store_verify=None)
    report = r.reap_once(dry_run=False)
    assert report.reaped == 0
    assert (src / "a.txt").exists()
    assert "NO-STORE-VERIFY" in open(log_path).read()


def test_reaper_skips_on_failed_store_verify(lane):
    from clouddog.reaper import compute_hash
    src, log_path, state_path = lane
    write(src, "a.txt", b"hello")
    manifest = {"a.txt": compute_hash(str(src / "a.txt"))}
    r = Reaper(str(src), manifest, log_path, state_path=state_path,
               store_verify=lambda rel, full: False)
    report = r.reap_once(dry_run=False)
    assert report.reaped == 0
    assert (src / "a.txt").exists()
