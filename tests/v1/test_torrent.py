"""Tests for clouddog.torrent — the delete-as-you-go torrent mirror engine.

Never downloads anything: live-engine tests use synthetic torrent_info
built from local files (the session hash-checks local data only; DHT and
networking are disabled in tests).
"""

import hashlib
import json
import warnings
from pathlib import Path

import pytest

import clouddog.torrent as t
from clouddog.errors import CloudDogError
from clouddog.stores.base import Health, HealthStatus, StoredRef

lt = pytest.importorskip("libtorrent", reason="libtorrent bindings required")


class FakeBackend:
    """Duck-typed StoreBackend: records uploads, verifies byte-exactness."""

    name = "fake"

    def __init__(self):
        self.stored: list[tuple[str, StoredRef]] = []

    def health(self) -> HealthStatus:
        return HealthStatus(Health.LIVE)

    def store(self, local_path: str, remote_ref: str, dry_run: bool = False) -> StoredRef:
        data = Path(local_path).read_bytes()
        ref = StoredRef(
            backend="fake",
            locator=f"{remote_ref}/{Path(local_path).name}",
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        self.stored.append((local_path, ref))
        return ref

    def verify(self, local_path: str, stored: StoredRef) -> bool:
        data = Path(local_path).read_bytes()
        return hashlib.sha256(data).hexdigest() == stored.sha256


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDDOG_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        t,
        "_DEFAULT_SESSION_PARAMS",
        {
            "listen_interfaces": "0.0.0.0:0",
            "enable_dht": False,
            "enable_upnp": False,
            "enable_natpmp": False,
        },
    )
    yield tmp_path
    t._reset_for_tests()


def make_torrent_info(data_dir: Path):
    """Build a torrent_info for files already on disk (no network).

    Uses libtorrent's deprecated creation helpers (suppressed): they are the
    only way to mint a synthetic torrent in the 2.1.x python bindings, and
    this function is test scaffolding only — the production module never
    creates torrents.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        fs = lt.file_storage()
        lt.add_files(fs, str(data_dir))
        ct = lt.create_torrent(fs)
        lt.set_piece_hashes(ct, str(data_dir.parent))
        return lt.torrent_info(ct.generate())


def seed_dir(tmp_path: Path, name: str = "data") -> Path:
    """A save dir containing complete torrent data (dir name = torrent root)."""
    save_path = tmp_path / "dl"
    data = save_path / name
    (data / "sub").mkdir(parents=True)
    (data / "a.bin").write_bytes(b"A" * 3000)
    (data / "sub" / "b.bin").write_bytes(b"B" * 3000)
    return save_path


def wait_seeded(handle, timeout: float = 15.0) -> None:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            ti = handle.torrent_file()
            if ti is None:
                continue
            prog = handle.file_progress()
            real = t._real_files(ti)
            if real and all(i < len(prog) and prog[i] >= size > 0 for i, _, size in real):
                return
        except Exception:  # noqa: BLE001, S110 - polling loop, transient errors retry
            pass
        time.sleep(0.2)
    raise AssertionError("synthetic torrent did not finish local hash check in time")


# -- libtorrent_status -------------------------------------------------------


def test_libtorrent_status_shape(home):
    st = t.libtorrent_status()
    assert set(st.keys()) == {"available", "reason", "version"}
    assert isinstance(st["available"], bool)
    assert st["available"] is True  # installed in this environment
    assert isinstance(st["version"], str) and st["version"]
    assert isinstance(st["reason"], str) and st["reason"]


def test_unavailable_path_is_honest(home, monkeypatch):
    monkeypatch.setattr(t, "_lt", None)
    st = t.libtorrent_status()
    assert st == {
        "available": False,
        "reason": st["reason"],
        "version": None,
    }
    assert "libtorrent" in st["reason"].lower()
    with pytest.raises(CloudDogError) as exc:
        t.add_magnet("magnet:?xt=urn:btih:abc", "github", "r", backend=FakeBackend())
    assert "torrent ingest unavailable" in str(exc.value)
    assert t.torrent_status() == []


# -- add_magnet validation ---------------------------------------------------


def test_add_magnet_rejects_bad_magnet(home):
    with pytest.raises(CloudDogError, match="not a magnet link"):
        t.add_magnet("https://example.com/file.torrent", "github", "r", backend=FakeBackend())


def test_add_magnet_rejects_unknown_tier(home):
    with pytest.raises(CloudDogError, match="unknown tier"):
        t.add_magnet("magnet:?xt=urn:btih:abc", "nope", "r")


def test_add_magnet_rejects_bad_backend_object(home):
    with pytest.raises(CloudDogError, match="missing required method"):
        t.add_magnet("magnet:?xt=urn:btih:abc", "github", "r", backend=object())


# -- session + status shape with a synthetic torrent --------------------------


def test_session_lifecycle_and_status_shape(home):
    save_path = seed_dir(home)
    ti = make_torrent_info(save_path / "data")
    backend = FakeBackend()
    tid = t._engine()._add_torrent_info(ti, "github", "owner/repo@tag", backend, str(save_path))
    try:
        assert isinstance(tid, str) and len(tid) == 16
        entries = t.torrent_status()
        assert len(entries) == 1
        e = entries[0]
        assert set(e.keys()) == {
            "id",
            "name",
            "progress_pct",
            "down_rate_Bps",
            "state",
            "pieces_done",
            "pieces_total",
        }
        assert e["id"] == tid
        assert isinstance(e["name"], str)
        assert 0.0 <= e["progress_pct"] <= 100.0
        assert isinstance(e["down_rate_Bps"], int)
        assert isinstance(e["state"], str) and e["state"]
        assert e["pieces_done"] >= 0 and e["pieces_total"] >= 0
    finally:
        t.remove_torrent(tid)
    assert t.torrent_status() == []


# -- state file round-trip ----------------------------------------------------


def test_state_file_round_trip(home):
    save_path = seed_dir(home)
    ti = make_torrent_info(save_path / "data")
    tid = t._engine()._add_torrent_info(
        ti, "github", "owner/repo@tag", FakeBackend(), str(save_path)
    )
    state_file = Path(home / "home" / "torrents.json")
    assert state_file.is_file()
    saved = json.loads(state_file.read_text())
    assert tid in saved
    assert saved[tid]["tier"] == "github"
    assert saved[tid]["remote_ref"] == "owner/repo@tag"
    assert saved[tid]["uploaded_files"] == []
    assert saved[tid]["done"] is False

    # simulate a process restart: drop in-memory handles, keep the state file
    t._reset_for_tests()
    entries = t.torrent_status()
    assert len(entries) == 1
    assert entries[0]["id"] == tid
    assert entries[0]["state"] == "stopped"
    t.remove_torrent(tid, delete_pieces=False)
    assert json.loads(state_file.read_text()) == {}


# -- piece-deletion helper -----------------------------------------------------


def test_delete_local_file_helper(home):
    f = home / "piece.bin"
    f.write_bytes(b"data")
    assert t._delete_local_file(str(f)) is True
    assert not f.exists()
    # missing file: no raise, returns False
    assert t._delete_local_file(str(f)) is False
    # a directory is not a piece file: never deleted
    d = home / "subdir"
    d.mkdir()
    assert t._delete_local_file(str(d)) is False
    assert d.is_dir()


# -- pump: completed files upload to the backend, then vanish locally ---------


def test_pump_uploads_completed_files_and_deletes_local(home):
    save_path = seed_dir(home)
    data = save_path / "data"
    ti = make_torrent_info(data)
    backend = FakeBackend()
    tid = t._engine()._add_torrent_info(ti, "terabox", "/remote/dir", backend, str(save_path))
    handle = t._engine()._registry[tid]["handle"]
    wait_seeded(handle)

    t._engine().pump_once()

    # both real files went to the backend exactly once...
    assert sorted(Path(p).name for p, _ in backend.stored) == ["a.bin", "b.bin"]
    for local_path, ref in backend.stored:
        assert ref.size == 3000
        assert ref.locator.startswith("/remote/dir/")
    # ...and the local copies are gone (delete-as-you-go)
    assert not (data / "a.bin").exists()
    assert not (data / "sub" / "b.bin").exists()

    saved = json.loads((home / "home" / "torrents.json").read_text())
    assert saved[tid]["done"] is True
    assert {u["file_path"] for u in saved[tid]["uploaded_files"]} == {
        "data/a.bin",
        "data/sub/b.bin",
    }
    # every stored ref recorded is byte-verifiable against what was uploaded
    for u in saved[tid]["uploaded_files"]:
        ref = StoredRef.from_dict(u["stored"])
        assert ref.backend == "fake" and ref.size == 3000

    # finished torrent detaches from the session; status shows the receipt
    entries = t.torrent_status()
    assert len(entries) == 1 and entries[0]["state"] == "completed"
    assert entries[0]["progress_pct"] == 100.0


# -- remove --------------------------------------------------------------------


def test_remove_torrent_unknown_id(home):
    with pytest.raises(CloudDogError, match="unknown torrent id"):
        t.remove_torrent("doesnotexist")


def test_remove_torrent_cleans_pieces(home):
    save_path = seed_dir(home)
    ti = make_torrent_info(save_path / "data")
    # L2: production piece dirs live under _pieces_root(); the test hook
    # must reflect that for the containment check to allow removal.
    piece_dir = t._pieces_root() / "testtid"
    piece_dir.mkdir(parents=True)
    tid = t._engine()._add_torrent_info(ti, "github", "r", FakeBackend(), str(piece_dir))
    t.remove_torrent(tid, delete_pieces=True)
    assert not piece_dir.exists()
    assert t.torrent_status() == []
