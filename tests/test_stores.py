"""Tests for clouddog.stores (sibling-owned) plus the test FakeStore."""

import pytest

from clouddog import stores as stores_mod
from tests.fake_store import FakeStore


def test_registry_has_expected_stores():
    # The GitHub/TeraBox/Kaggle backends live in the cloud_dog.py v2 engine;
    # this package's registry holds the backends v2 does not have.
    assert set(stores_mod.REGISTRY) == {"drive"}


def test_get_store_unknown_name_raises():
    with pytest.raises(stores_mod.StoreError):
        stores_mod.get_store("nope", {})


def test_run_with_retries_eventually_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise stores_mod.StoreError("boom")
        return "ok"

    assert stores_mod.run_with_retries(flaky, attempts=5, base_delay=0.001, what="t") == "ok"
    assert len(calls) == 3


def test_run_with_retries_reraises_last_failure():
    def always():
        raise stores_mod.StoreError("nope")

    with pytest.raises(stores_mod.StoreError):
        stores_mod.run_with_retries(always, attempts=2, base_delay=0.001, what="t")


def test_fake_store_upload_verify_exists_roundtrip(tmp_path):
    fake = FakeStore({})
    target = tmp_path / "a.bin"
    target.write_bytes(b"hello cloud")
    ref = fake.upload(str(target), remote_dir="/CloudDog")
    assert ref.size == 11
    assert ref.remote_path == "/CloudDog/a.bin"
    assert fake.verify(ref) is True
    assert fake.exists("/CloudDog", "a.bin") == ref
    assert fake.uri_for(ref) == f"fake:{ref.remote_path}"


def test_fake_store_verify_rejects_tampered_ref(tmp_path):
    fake = FakeStore({})
    target = tmp_path / "a.bin"
    target.write_bytes(b"hello")
    ref = fake.upload(str(target))
    bad = stores_mod.StoredRef(
        store=ref.store,
        remote_path=ref.remote_path,
        size=ref.size,
        sha256="0" * 64,
        uri=ref.uri,
    )
    assert fake.verify(bad) is False
    assert fake.exists("", "missing.bin") is None
