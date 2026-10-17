"""Tests for the CloudDog store backends.

All subprocess and filesystem-probing calls are faked via monkeypatch —
no real ``gh``, ``tbupload.py``, or ``rclone`` is ever executed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from clouddog.errors import CloudDogError, StoreDegraded
from clouddog.stores.base import Health, StoredRef
from clouddog.stores.drive import DriveBackend
from clouddog.stores.github import GitHubReleaseBackend
from clouddog.stores.terabox import TB_UPLOADER, TeraBoxBackend

FILE_BYTES = b"clouddog-store-backend-test-bytes-001"
FILE_SHA256 = hashlib.sha256(FILE_BYTES).hexdigest()
FILE_NAME = "test-asset.bin"
REPO = "octo/repo"
TAG = "v1"


@pytest.fixture()
def local_file(tmp_path):
    path = tmp_path / FILE_NAME
    path.write_bytes(FILE_BYTES)
    return str(path)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Keep retry backoff from slowing tests."""
    monkeypatch.setattr("time.sleep", lambda _s: None)


# ---------------------------------------------------------------------- #
# GitHub backend fakes
# ---------------------------------------------------------------------- #
def make_gh_fake(
    *,
    auth_ok=True,
    view_ok=True,
    asset_size=None,
    download_bytes=None,
    api_failures=0,
    asset_exists=True,
):
    """Fake ``subprocess.run`` for the gh CLI.

    api_failures: number of initial ``gh api`` calls that fail transiently
    (503) before succeeding — exercises the retry path.
    asset_exists: whether the fake release lists the asset (default True
    preserves the old collision-path tests; set False for fresh-upload tests).
    """
    calls = []
    state = {"api_calls": 0}

    def fake(args, **kwargs):
        calls.append(list(args))
        assert args[0] == "gh", f"expected gh, got {args[0]}"
        cmd = args[1]
        if cmd == "auth":
            if auth_ok:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "error: not logged in to any hosts")
        if cmd == "release":
            sub = args[2]
            if sub == "view":
                if view_ok:
                    return subprocess.CompletedProcess(args, 0, "{}", "")
                return subprocess.CompletedProcess(args, 1, "", "release not found")
            if sub == "create":
                return subprocess.CompletedProcess(args, 0, "{}", "")
            if sub == "upload":
                return subprocess.CompletedProcess(args, 0, "", "")
            if sub == "download":
                argv = list(args)
                name = argv[argv.index("-p") + 1]
                dest = argv[argv.index("-D") + 1]
                data = FILE_BYTES if download_bytes is None else download_bytes
                with open(os.path.join(dest, name), "wb") as fh:
                    fh.write(data)
                return subprocess.CompletedProcess(args, 0, "", "")
        if cmd == "api":
            state["api_calls"] += 1
            if state["api_calls"] <= api_failures:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 503: Service Unavailable")
            size = len(FILE_BYTES) if asset_size is None else asset_size
            assets = [{"name": FILE_NAME, "size": size}] if asset_exists else []
            releases = [
                {
                    "tag_name": TAG,
                    "assets": assets,
                }
            ]
            return subprocess.CompletedProcess(args, 0, json.dumps(releases), "")
        return subprocess.CompletedProcess(args, 1, "", f"unexpected: {args}")

    fake.calls = calls
    return fake


def patch_gh(monkeypatch, fake):
    monkeypatch.setattr(subprocess, "run", fake)


# ---------------------------------------------------------------------- #
# GitHub: health
# ---------------------------------------------------------------------- #
def test_github_health_live(monkeypatch):
    patch_gh(monkeypatch, make_gh_fake(auth_ok=True))
    status = GitHubReleaseBackend().health()
    assert status.health is Health.LIVE


def test_github_health_down(monkeypatch):
    patch_gh(monkeypatch, make_gh_fake(auth_ok=False))
    status = GitHubReleaseBackend().health()
    assert status.health is Health.DOWN
    assert "gh not authenticated" in status.reason


# ---------------------------------------------------------------------- #
# GitHub: store
# ---------------------------------------------------------------------- #
def test_github_store_success(monkeypatch, local_file):
    fake = make_gh_fake(view_ok=False, asset_exists=False)  # release missing -> create runs
    patch_gh(monkeypatch, fake)
    ref = GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")
    assert ref.backend == "github"
    assert ref.locator == f"{REPO}@{TAG}/{FILE_NAME}"
    assert ref.size == len(FILE_BYTES)
    assert ref.sha256 == FILE_SHA256
    assert ref.extra["parts"] == 1
    # Never deletes the local file.
    assert os.path.isfile(local_file)
    # Release was created (view failed -> create ran) and asset uploaded.
    subcmds = [c[1:3] for c in fake.calls]
    assert ["release", "create"] in subcmds
    assert ["release", "upload"] in subcmds


def test_github_store_existing_release(monkeypatch, local_file):
    fake = make_gh_fake(view_ok=True)
    patch_gh(monkeypatch, fake)
    GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")
    subcmds = [c[1:3] for c in fake.calls]
    assert ["release", "create"] not in subcmds


def test_github_store_retries_transient_api(monkeypatch, local_file):
    fake = make_gh_fake(api_failures=2)
    patch_gh(monkeypatch, fake)
    ref = GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")
    assert ref.sha256 == FILE_SHA256


def test_github_store_asset_size_mismatch(monkeypatch, local_file):
    # L5: a same-named asset with different content is refused, not clobbered.
    fake = make_gh_fake(asset_size=len(FILE_BYTES) + 1)
    patch_gh(monkeypatch, fake)
    with pytest.raises(CloudDogError):
        GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")


def test_github_store_sha256_mismatch(monkeypatch, local_file):
    corrupted = b"corrupted-content-!!!"  # different bytes...
    corrupted += b"\x00" * (len(FILE_BYTES) - len(corrupted))  # ...same length
    assert len(corrupted) == len(FILE_BYTES)
    fake = make_gh_fake(download_bytes=corrupted)
    patch_gh(monkeypatch, fake)
    # L5: same size but different content -> refuse, not clobber.
    with pytest.raises(CloudDogError):
        GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")


def test_github_store_reuses_identical_asset(monkeypatch, local_file):
    # L5: a same-named asset that is byte-identical is reused, not re-uploaded.
    fake = make_gh_fake(view_ok=True)
    patch_gh(monkeypatch, fake)
    ref = GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")
    subcmds = [c[1:3] for c in fake.calls]
    assert ["release", "upload"] not in subcmds
    assert ref.extra.get("reused") is True
    assert ref.sha256 == FILE_SHA256


def test_github_store_dry_run_no_network(monkeypatch, local_file):
    def boom(args, **kwargs):
        raise AssertionError("network must not be touched in dry_run")

    monkeypatch.setattr(subprocess, "run", boom)
    ref = GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}", dry_run=True)
    assert ref.locator.startswith("dry-run:")
    assert ref.sha256 == FILE_SHA256
    assert ref.size == len(FILE_BYTES)


def test_github_store_degraded_raises(monkeypatch, local_file):
    patch_gh(monkeypatch, make_gh_fake(auth_ok=False))
    with pytest.raises(StoreDegraded):
        GitHubReleaseBackend().store(local_file, f"{REPO}@{TAG}")


@pytest.mark.parametrize(
    "bad_ref", ["nope", "owner@", "@tag", "owner/repo", "owner/repo@tag/extra", ""]
)
def test_github_bad_remote_ref(monkeypatch, local_file, bad_ref):
    patch_gh(monkeypatch, make_gh_fake())
    with pytest.raises(CloudDogError):
        GitHubReleaseBackend().store(local_file, bad_ref)


# ---------------------------------------------------------------------- #
# GitHub: verify
# ---------------------------------------------------------------------- #
def _stored_ref():
    return StoredRef(
        backend="github",
        locator=f"{REPO}@{TAG}/{FILE_NAME}",
        size=len(FILE_BYTES),
        sha256=FILE_SHA256,
        extra={"parts": 1, "asset_names": [FILE_NAME]},
    )


def test_github_verify_true(monkeypatch, local_file):
    patch_gh(monkeypatch, make_gh_fake())
    assert GitHubReleaseBackend().verify(local_file, _stored_ref()) is True


def test_github_verify_false_on_mismatch(monkeypatch, local_file):
    corrupted = b"tampered" + b"\x00" * (len(FILE_BYTES) - len(b"tampered"))
    patch_gh(monkeypatch, make_gh_fake(download_bytes=corrupted))
    assert GitHubReleaseBackend().verify(local_file, _stored_ref()) is False


def test_github_verify_bad_locator(monkeypatch, local_file):
    patch_gh(monkeypatch, make_gh_fake())
    bad = StoredRef(backend="github", locator="garbage", size=1, sha256="x", extra={})
    with pytest.raises(CloudDogError):
        GitHubReleaseBackend().verify(local_file, bad)


# ---------------------------------------------------------------------- #
# TeraBox backend fakes
# ---------------------------------------------------------------------- #
def make_tb_fake(
    *, resolve_ok=True, resolve_err="errno -6 need verify", ls_entries=None, upload_ok=True
):
    calls = []

    def fake(args, **kwargs):
        calls.append(list(args))
        assert args[0] == sys.executable and args[1] == TB_UPLOADER
        cmd = args[2]
        if cmd == "resolve":
            if resolve_ok:
                return subprocess.CompletedProcess(args, 0, "jsToken OK", "")
            return subprocess.CompletedProcess(args, 1, "", resolve_err)
        if cmd == "upload":
            if upload_ok:
                return subprocess.CompletedProcess(args, 0, "upload OK", "")
            return subprocess.CompletedProcess(args, 1, "", "upload failed")
        if cmd == "ls":
            lines = "".join(f"f {size:>12} {name}\n" for name, size in (ls_entries or []))
            return subprocess.CompletedProcess(args, 0, lines, "")
        return subprocess.CompletedProcess(args, 1, "", f"unexpected: {args}")

    fake.calls = calls
    return fake


def patch_tb(monkeypatch, fake, *, uploader_exists=True, creds_exist=True):
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        os.path,
        "exists",
        lambda p: (
            (p == TB_UPLOADER and uploader_exists) or (p.endswith(".creds.json") and creds_exist)
        ),
    )


# ---------------------------------------------------------------------- #
# TeraBox: health
# ---------------------------------------------------------------------- #
def test_terabox_health_down_no_uploader(monkeypatch):
    patch_tb(monkeypatch, make_tb_fake(), uploader_exists=False)
    status = TeraBoxBackend().health()
    assert status.health is Health.DOWN


def test_terabox_health_degraded_no_creds(monkeypatch):
    patch_tb(monkeypatch, make_tb_fake(), creds_exist=False)
    status = TeraBoxBackend().health()
    assert status.health is Health.DEGRADED
    assert "setup_creds.py" in status.reason


def test_terabox_health_live(monkeypatch):
    patch_tb(monkeypatch, make_tb_fake(resolve_ok=True))
    assert TeraBoxBackend().health().health is Health.LIVE


def test_terabox_health_degraded_resolve_fail(monkeypatch):
    patch_tb(monkeypatch, make_tb_fake(resolve_ok=False, resolve_err="errno -6 need verify"))
    status = TeraBoxBackend().health()
    assert status.health is Health.DEGRADED
    assert "errno -6 need verify" in status.reason  # reason passthrough


# ---------------------------------------------------------------------- #
# TeraBox: store / verify
# ---------------------------------------------------------------------- #
def test_terabox_store_success(monkeypatch, local_file):
    entries = [(FILE_NAME, len(FILE_BYTES))]
    patch_tb(monkeypatch, make_tb_fake(ls_entries=entries))
    ref = TeraBoxBackend().store(local_file, "/CN-Gigaleak/_test")
    assert ref.backend == "terabox"
    assert ref.locator == f"/CN-Gigaleak/_test/{FILE_NAME}"
    assert ref.size == len(FILE_BYTES)
    assert ref.sha256 == FILE_SHA256
    assert os.path.isfile(local_file)  # never deletes


def test_terabox_store_degraded_raises(monkeypatch, local_file):
    patch_tb(monkeypatch, make_tb_fake(), creds_exist=False)
    with pytest.raises(StoreDegraded):
        TeraBoxBackend().store(local_file, "/CN-Gigaleak/_test")


def test_terabox_store_verify_mismatch(monkeypatch, local_file):
    patch_tb(monkeypatch, make_tb_fake(ls_entries=[(FILE_NAME, 1)]))
    with pytest.raises(CloudDogError):
        TeraBoxBackend().store(local_file, "/CN-Gigaleak/_test")


def test_terabox_bad_remote_ref(monkeypatch, local_file):
    patch_tb(monkeypatch, make_tb_fake())
    with pytest.raises(CloudDogError):
        TeraBoxBackend().store(local_file, "not-a-dir")


def test_terabox_verify_true(monkeypatch, local_file):
    patch_tb(monkeypatch, make_tb_fake(ls_entries=[(FILE_NAME, len(FILE_BYTES))]))
    stored = StoredRef(
        backend="terabox",
        locator=f"/CN-Gigaleak/_test/{FILE_NAME}",
        size=len(FILE_BYTES),
        sha256=FILE_SHA256,
    )
    assert TeraBoxBackend().verify(local_file, stored) is True


def test_terabox_verify_false(monkeypatch, local_file):
    patch_tb(monkeypatch, make_tb_fake(ls_entries=[("other.bin", 9)]))
    stored = StoredRef(
        backend="terabox",
        locator=f"/CN-Gigaleak/_test/{FILE_NAME}",
        size=len(FILE_BYTES),
        sha256=FILE_SHA256,
    )
    assert TeraBoxBackend().verify(local_file, stored) is False


# ---------------------------------------------------------------------- #
# Drive backend fakes
# ---------------------------------------------------------------------- #
def make_rclone_fake(*, which="rclone", lsd_ok=True, copyto_ok=True, check_ok=True):
    calls = []

    def fake_which(name):
        calls.append(("which", name))
        return which if name == "rclone" else None

    def fake_run(args, **kwargs):
        calls.append(list(args))
        assert args[0] == "rclone"
        sub = args[1]
        if sub == "lsd":
            if lsd_ok:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "failed to list")
        if sub == "copyto":
            if copyto_ok:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "copy failed")
        if sub == "check":
            if check_ok:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 1, "", "differences found")
        return subprocess.CompletedProcess(args, 1, "", f"unexpected: {args}")

    fake_run.calls = calls
    return fake_which, fake_run


def patch_drive(monkeypatch, which, run):
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(subprocess, "run", run)
    # Hide the CloudDog-managed install so `which` fully controls resolution.
    monkeypatch.setattr("clouddog.stores.drive.MANAGED_RCLONE_BIN", "/nonexistent/rclone")
    # Hide the CloudDog-managed install so `which` fully controls resolution.
    monkeypatch.setattr("clouddog.stores.drive.MANAGED_RCLONE_BIN", "/nonexistent/rclone")


# ---------------------------------------------------------------------- #
# Drive: health / store / verify
# ---------------------------------------------------------------------- #
def test_drive_health_degraded_no_rclone(monkeypatch):
    which, run = make_rclone_fake(which=None)
    patch_drive(monkeypatch, which, run)
    status = DriveBackend().health()
    assert status.health is Health.DEGRADED
    assert "rclone not installed" in status.reason


def test_drive_health_live(monkeypatch):
    which, run = make_rclone_fake()
    patch_drive(monkeypatch, which, run)
    assert DriveBackend().health().health is Health.LIVE


def test_drive_health_degraded_lsd_fail(monkeypatch):
    which, run = make_rclone_fake(lsd_ok=False)
    patch_drive(monkeypatch, which, run)
    status = DriveBackend().health()
    assert status.health is Health.DEGRADED
    assert "failed to list" in status.reason


def test_drive_store_success(monkeypatch, local_file):
    which, run = make_rclone_fake()
    patch_drive(monkeypatch, which, run)
    ref = DriveBackend().store(local_file, "drive:backups/" + FILE_NAME)
    assert ref.backend == "drive"
    assert ref.locator == "drive:backups/" + FILE_NAME
    assert ref.sha256 == FILE_SHA256
    assert ref.size == len(FILE_BYTES)
    assert os.path.isfile(local_file)
    copyto = [c for c in run.calls if isinstance(c, list) and c[1] == "copyto"]
    assert copyto and "--checksum" in copyto[0]


def test_drive_store_default_remote(monkeypatch, local_file, tmp_path):
    which, run = make_rclone_fake()
    patch_drive(monkeypatch, which, run)
    monkeypatch.setenv("CLOUDEDOG_DRIVE_REMOTE", "gdrive:")
    ref = DriveBackend().store(local_file, "backups/" + FILE_NAME)
    assert ref.locator == "gdrive:backups/" + FILE_NAME


def test_drive_store_check_fail(monkeypatch, local_file):
    which, run = make_rclone_fake(check_ok=False)
    patch_drive(monkeypatch, which, run)
    with pytest.raises(CloudDogError):
        DriveBackend().store(local_file, "drive:backups/" + FILE_NAME)


def test_drive_store_empty_ref(monkeypatch, local_file):
    which, run = make_rclone_fake()
    patch_drive(monkeypatch, which, run)
    with pytest.raises(CloudDogError):
        DriveBackend().store(local_file, "")


def test_drive_verify_true_false(monkeypatch, local_file):
    which, run = make_rclone_fake(check_ok=True)
    patch_drive(monkeypatch, which, run)
    stored = StoredRef(
        backend="drive",
        locator="drive:backups/" + FILE_NAME,
        size=len(FILE_BYTES),
        sha256=FILE_SHA256,
    )
    assert DriveBackend().verify(local_file, stored) is True

    which2, run2 = make_rclone_fake(check_ok=False)
    patch_drive(monkeypatch, which2, run2)
    assert DriveBackend().verify(local_file, stored) is False


def test_drive_dry_run_no_network(monkeypatch, local_file):
    def boom(args, **kwargs):
        raise AssertionError("network must not be touched in dry_run")

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(shutil, "which", lambda _n: "/usr/bin/rclone")
    ref = DriveBackend().store(local_file, "drive:x", dry_run=True)
    assert ref.locator.startswith("dry-run:")
    assert ref.sha256 == FILE_SHA256


# ---------------------------------------------------------------------- #
# StoredRef serialization round-trip
# ---------------------------------------------------------------------- #
def test_storedref_round_trip():
    ref = StoredRef(
        backend="github",
        locator=f"{REPO}@{TAG}/{FILE_NAME}",
        size=len(FILE_BYTES),
        sha256=FILE_SHA256,
        extra={"parts": 2, "asset_names": ["a.part-aa", "a.part-ab"]},
    )
    assert StoredRef.from_dict(ref.to_dict()) == ref
