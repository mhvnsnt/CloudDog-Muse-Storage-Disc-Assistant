"""Torrent mirror engine — delete-as-you-go torrent ingest into store tiers.

Design (file-granular, documented choice):
  Pieces stream into a temp piece dir under ~/.clouddog/torrent-pieces/<id>/.
  A background pump thread watches each torrent; as soon as an individual FILE
  is complete it is uploaded to the store backend via ``backend.store()``
  (which byte-verifies before returning, per the StoreBackend contract) and the
  local copy is deleted IMMEDIATELY — the delete-as-you-go pattern from the
  DELETE-AS-YOU-GO law. Uploaded files get libtorrent file-priority 0 so they
  are never re-downloaded, and are recorded in ~/.clouddog/torrents.json so a
  restart resumes without re-uploading.

  Why file-granular instead of piece-granular: piece-granular would require
  reconstructing files from raw pieces and hand-managing the piece picker
  (``read_piece``/``have_piece`` round-trips), which is fragile against
  libtorrent's storage layer and buys nothing — a multi-GB torrent's files are
  individually far smaller than the whole, and the local piece dir never holds
  more than the in-flight remainder. Correct and simple beats clever.

Honesty contract: this module NEVER fakes a download. If the libtorrent
bindings are not importable, ``libtorrent_status()`` reports it,
``add_magnet()`` raises, and ``torrent_status()`` returns [].
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import warnings
from pathlib import Path
from typing import Any, Callable

from clouddog.errors import CloudDogError

log = logging.getLogger(__name__)

try:
    import libtorrent as _lt  # type: ignore
except Exception as _lt_err:  # ImportError and friends — stay importable regardless
    _lt = None  # type: ignore
    _LT_IMPORT_ERROR = f"{type(_lt_err).__name__}: {_lt_err}"
else:
    _LT_IMPORT_ERROR = ""

# Deploy note: manylinux wheels exist for common CPython versions
# (tested: libtorrent 2.1.1 cp312 manylinux x86_64). On PEP-668 systems
# (Debian/Ubuntu) use a venv or `pip install --user --break-system-packages`.
_INSTALL_HINT = (
    "install the libtorrent python bindings with 'pip install libtorrent' "
    "(needs a manylinux wheel for your python version / OS)"
)

_STATE_FILENAME = "torrents.json"
_PIECES_DIRNAME = "torrent-pieces"
_PUMP_INTERVAL_S = 5.0

# Tier -> (module, class) resolved lazily so store backends can be built
# in parallel with this module. ImportError is reported honestly.
_TIER_BACKENDS: dict[str, tuple[str, str]] = {
    "github": ("clouddog.stores.github", "GitHubReleaseBackend"),
    "terabox": ("clouddog.stores.terabox", "TeraBoxBackend"),
    "drive": ("clouddog.stores.drive", "DriveBackend"),
}

_DEFAULT_SESSION_PARAMS: dict[str, Any] = {
    "listen_interfaces": "0.0.0.0:0",  # ephemeral port, no fixed-port conflicts
    "enable_dht": True,  # trackerless magnets need DHT for peer discovery
}

# libtorrent torrent_status.state -> human string
_STATE_NAMES = {
    0: "queued_for_checking",
    1: "checking_files",
    2: "downloading_metadata",
    3: "downloading",
    4: "finished",
    5: "seeding",
    6: "allocating",
    7: "checking_resume_data",
}


def _home() -> Path:
    """CloudDog home dir; CLOUDDOG_HOME env overrides (used by tests)."""
    return Path(os.environ.get("CLOUDDOG_HOME", str(Path.home() / ".clouddog")))


def _state_path() -> Path:
    return _home() / _STATE_FILENAME


def _pieces_root() -> Path:
    return _home() / _PIECES_DIRNAME


def libtorrent_status() -> dict:
    """Report whether real torrent ingest is possible in this environment."""
    if _lt is None:
        return {
            "available": False,
            "reason": f"libtorrent not importable ({_LT_IMPORT_ERROR}); {_INSTALL_HINT}",
            "version": None,
        }
    return {
        "available": True,
        "reason": "libtorrent bindings importable",
        "version": str(_lt.__version__),
    }


def _require_lt() -> Any:
    if _lt is None:
        raise CloudDogError(
            f"torrent ingest unavailable: {libtorrent_status()['reason']}"
        )
    return _lt


def _resolve_backend(tier: str, backend: Any) -> Any:
    """Return a usable backend: the passed object, or lazily resolved by tier."""
    if backend is not None:
        for meth in ("health", "store", "verify"):
            if not callable(getattr(backend, meth, None)):
                raise CloudDogError(
                    f"backend object for tier '{tier}' is missing required "
                    f"method '{meth}()' (needs health/store/verify)"
                )
        return backend
    if tier not in _TIER_BACKENDS:
        raise CloudDogError(
            f"unknown tier '{tier}' and no backend given "
            f"(known tiers: {sorted(_TIER_BACKENDS)})"
        )
    mod_name, cls_name = _TIER_BACKENDS[tier]
    try:
        mod = __import__(mod_name, fromlist=[cls_name])
    except ImportError as e:
        raise CloudDogError(
            f"tier '{tier}' backend module '{mod_name}' is not built yet: {e}"
        ) from e
    try:
        cls = getattr(mod, cls_name)
    except AttributeError as e:
        raise CloudDogError(
            f"tier '{tier}' module '{mod_name}' has no class '{cls_name}'"
        ) from e
    return cls()


def _delete_local_file(path: str) -> bool:
    """Delete one local file. Returns True if it existed and was removed.

    Never raises for missing files; logs (does not hide) real OS errors.
    """
    p = Path(path)
    try:
        if p.is_file() or p.is_symlink():
            p.unlink()
            log.info("deleted local piece file %s", path)
            return True
        return False
    except OSError as e:
        log.warning("could not delete local file %s: %s", path, e)
        return False


def _is_pad_file(torrent_path: str) -> bool:
    return ".pad" in Path(torrent_path).parts


def _real_files(ti: Any) -> list[tuple[int, str, int]]:
    """(index, torrent-relative path, size) for non-pad files.

    NOTE on libtorrent 2.1.x python bindings: every file-enumeration accessor
    (ti.files(), ti.orig_files(), ti.file_at()) is flagged DeprecationWarning,
    but no warning-free replacement is exposed yet — the underlying APIs are
    stable and correct. The warnings are suppressed here (and only here) so
    production logs stay clean; revisit when the bindings ship the rename.
    """
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        files = ti.files()
        for i, f in enumerate(files):
            if _is_pad_file(f.path):
                continue
            out.append((i, f.path, f.size))
    return out


class _TorrentEngine:
    """Owns the libtorrent session, the pump thread, and the state file."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Any = None
        self._registry: dict[str, dict[str, Any]] = {}
        self._pump_thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- session / thread lifecycle -------------------------------------

    def _ensure_session(self, lt: Any) -> Any:
        with self._lock:
            if self._session is None:
                self._session = lt.session(_DEFAULT_SESSION_PARAMS)
                log.info("libtorrent session started (v%s)", lt.__version__)
            return self._session

    def _ensure_pump(self) -> None:
        with self._lock:
            if self._pump_thread is None or not self._pump_thread.is_alive():
                self._stop.clear()
                self._pump_thread = threading.Thread(
                    target=self._pump_loop, name="clouddog-torrent-pump", daemon=True
                )
                self._pump_thread.start()

    def _pump_loop(self) -> None:
        while not self._stop.wait(_PUMP_INTERVAL_S):
            try:
                self.pump_once()
            except Exception:  # pump must never die on one bad torrent
                log.exception("torrent pump iteration failed")

    def shutdown(self) -> None:
        with self._lock:
            self._stop.set()
            thread, self._pump_thread = self._pump_thread, None
            session, self._session = self._session, None
            self._registry.clear()
        if thread is not None:
            thread.join(timeout=10)
        if session is not None:
            try:
                session.pause()
            except Exception:
                pass

    # -- state file ------------------------------------------------------

    def _load_state(self) -> dict[str, dict[str, Any]]:
        try:
            raw = _state_path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("torrent state file corrupt (%s); starting fresh", e)
            return {}
        return data if isinstance(data, dict) else {}

    def _save_state(self, state: dict[str, dict[str, Any]]) -> None:
        _home().mkdir(parents=True, exist_ok=True)
        tmp = _state_path().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(_state_path())

    # -- add --------------------------------------------------------------

    @staticmethod
    def _tid_for(magnet: str) -> str:
        return hashlib.sha1(magnet.encode("utf-8")).hexdigest()[:16]

    def add(self, magnet: str, tier: str, remote_ref: str, backend: Any) -> str:
        lt = _require_lt()
        if not magnet.startswith("magnet:?"):
            raise CloudDogError(f"not a magnet link: {magnet[:60]!r}")
        backend = _resolve_backend(tier, backend)

        tid = self._tid_for(magnet)
        with self._lock:
            if tid in self._registry:
                return tid  # idempotent re-add
            state = self._load_state()
            if tid in state and not state[tid].get("done"):
                self._resume_entry(lt, tid, state[tid], backend)
                return tid
            piece_dir = str(_pieces_root() / tid)
            os.makedirs(piece_dir, exist_ok=True)
            session = self._ensure_session(lt)
            handle = lt.add_magnet_uri(session, magnet, {"save_path": piece_dir})
            entry = {
                "magnet": magnet,
                "tier": tier,
                "remote_ref": remote_ref,
                "piece_dir": piece_dir,
                "created_at": time.time(),
                "done": False,
                "uploaded_files": [],  # [{file_path, stored: StoredRef dict}]
                "handle": handle,
                "backend": backend,
            }
            self._registry[tid] = entry
            state[tid] = {k: v for k, v in entry.items() if k not in ("handle", "backend")}
            self._save_state(state)
            self._ensure_pump()
            log.info("torrent %s added (tier=%s)", tid, tier)
            return tid

    def _resume_entry(self, lt: Any, tid: str, saved: dict[str, Any], backend: Any) -> None:
        """Re-attach a persisted (not done) torrent after a restart."""
        piece_dir = saved["piece_dir"]
        os.makedirs(piece_dir, exist_ok=True)
        session = self._ensure_session(lt)
        handle = lt.add_magnet_uri(session, saved["magnet"], {"save_path": piece_dir})
        uploaded = {u["file_path"] for u in saved.get("uploaded_files", [])}
        self._apply_uploaded_priorities(handle, uploaded)
        self._registry[tid] = {**saved, "handle": handle, "backend": backend}
        self._ensure_pump()
        log.info("torrent %s resumed from state (%d files already uploaded)",
                 tid, len(uploaded))

    @staticmethod
    def _apply_uploaded_priorities(handle: Any, uploaded_paths: set[str]) -> None:
        """Priority-0 files already in the store so they are never re-fetched."""
        try:
            ti = handle.torrent_file()
        except Exception:
            return
        if ti is None:
            return
        for i, path, _size in _real_files(ti):
            if path in uploaded_paths:
                try:
                    handle.file_priority(i, 0)
                except Exception:
                    log.warning("could not set priority 0 on %s", path)

    def _add_torrent_info(self, ti: Any, tier: str, remote_ref: str,
                          backend: Any, save_path: str) -> str:
        """Test/utility hook: register an already-known torrent_info (no network)."""
        lt = _require_lt()
        backend = _resolve_backend(tier, backend)
        tid = hashlib.sha1(ti.info_hashes().get_best().to_bytes()).hexdigest()[:16]
        with self._lock:
            session = self._ensure_session(lt)
            atp = lt.add_torrent_params()
            atp.ti = ti
            atp.save_path = save_path
            handle = session.add_torrent(atp)
            entry = {
                "magnet": f"synthetic:{tid}",
                "tier": tier,
                "remote_ref": remote_ref,
                "piece_dir": save_path,
                "created_at": time.time(),
                "done": False,
                "uploaded_files": [],
                "handle": handle,
                "backend": backend,
            }
            self._registry[tid] = entry
            state = self._load_state()
            state[tid] = {k: v for k, v in entry.items() if k not in ("handle", "backend")}
            self._save_state(state)
            return tid

    # -- pump --------------------------------------------------------------

    def pump_once(self) -> None:
        """One delete-as-you-go pass: upload each newly-completed file, then delete it."""
        with self._lock:
            items = list(self._registry.items())
        for tid, entry in items:
            try:
                self._pump_torrent(tid, entry)
            except Exception:
                log.exception("pump failed for torrent %s", tid)

    def _pump_torrent(self, tid: str, entry: dict[str, Any]) -> None:
        handle = entry["handle"]
        backend = entry["backend"]
        try:
            ti = handle.torrent_file()
        except Exception:
            return
        if ti is None:  # metadata not fetched yet
            return
        try:
            progress = handle.file_progress()
        except Exception:
            return
        uploaded = {u["file_path"] for u in entry["uploaded_files"]}
        changed = False
        for i, rel_path, size in _real_files(ti):
            if rel_path in uploaded:
                continue
            if size <= 0:
                continue  # zero-byte files carry nothing; skip the store round-trip
            if i >= len(progress) or progress[i] < size:
                continue  # not complete yet
            local_path = os.path.join(entry["piece_dir"], rel_path)
            if not os.path.isfile(local_path):
                continue
            stored = backend.store(local_path, entry["remote_ref"])
            # store() byte-verifies before returning (StoreBackend contract);
            # only now is it safe to delete the local copy.
            if _delete_local_file(local_path):
                try:
                    handle.file_priority(i, 0)
                except Exception:
                    log.warning("could not deprioritize uploaded file %s", rel_path)
                entry["uploaded_files"].append(
                    {"file_path": rel_path, "stored": stored.to_dict()}
                )
                changed = True
                log.info("torrent %s: uploaded+deleted %s", tid, rel_path)
        if changed:
            with self._lock:
                state = self._load_state()
                if tid in state:
                    state[tid]["uploaded_files"] = entry["uploaded_files"]
                    real_total = len(_real_files(ti))
                    if len(entry["uploaded_files"]) >= real_total and real_total > 0:
                        state[tid]["done"] = True
                        entry["done"] = True
                        self._finish_torrent(tid, entry)
                    self._save_state(state)

    def _finish_torrent(self, tid: str, entry: dict[str, Any]) -> None:
        """All files are in the store: detach from the session, keep the receipt."""
        try:
            if self._session is not None:
                self._session.remove_torrent(entry["handle"])
        except Exception:
            log.warning("remove_torrent failed for %s", tid, exc_info=True)
        self._registry.pop(tid, None)
        log.info("torrent %s complete: all files stored, session detached", tid)

    # -- status / remove -----------------------------------------------------

    def status_list(self) -> list[dict]:
        lt = _require_lt()
        out: list[dict] = []
        with self._lock:
            live = dict(self._registry)
            persisted = self._load_state()
        for tid, entry in live.items():
            handle = entry["handle"]
            try:
                st = handle.status()
            except Exception:
                continue
            out.append({
                "id": tid,
                "name": st.name or tid,
                "progress_pct": round(st.progress * 100, 2),
                "down_rate_Bps": int(st.download_rate),
                "state": _STATE_NAMES.get(int(st.state), str(st.state)),
                "pieces_done": max(0, int(st.num_complete)),
                "pieces_total": max(0, int(st.num_pieces)),
            })
        for tid, saved in persisted.items():
            if tid in live:
                continue
            out.append({
                "id": tid,
                "name": saved.get("magnet", tid)[:60],
                "progress_pct": 100.0 if saved.get("done") else 0.0,
                "down_rate_Bps": 0,
                "state": "completed" if saved.get("done") else "stopped",
                "pieces_done": 0,
                "pieces_total": 0,
            })
        return out

    def remove(self, tid: str, delete_pieces: bool = True) -> None:
        _require_lt()
        with self._lock:
            entry = self._registry.pop(tid, None)
            state = self._load_state()
            saved = state.pop(tid, None)
            self._save_state(state)
        if entry is not None:
            try:
                if self._session is not None:
                    self._session.remove_torrent(entry["handle"])
            except Exception:
                log.warning("remove_torrent failed for %s", tid, exc_info=True)
        piece_dir = (entry or saved or {}).get("piece_dir")
        if delete_pieces and piece_dir:
            shutil.rmtree(piece_dir, ignore_errors=True)
            log.info("torrent %s: piece dir removed", tid)
        if entry is None and saved is None:
            raise CloudDogError(f"unknown torrent id: {tid}")


_ENGINE = _TorrentEngine()


def _engine() -> _TorrentEngine:
    return _ENGINE


def _reset_for_tests() -> None:
    """Tear down the singleton (tests only)."""
    _ENGINE.shutdown()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add_magnet(magnet: str, tier: str, remote_ref: str, backend: Any = None) -> str:
    """Start mirroring a magnet link straight into a store tier.

    Returns the torrent id. Raises CloudDogError if libtorrent is unavailable,
    the magnet is malformed, or no backend can be resolved.
    """
    return _engine().add(magnet, tier, remote_ref, backend)


def torrent_status() -> list[dict]:
    """[{id, name, progress_pct, down_rate_Bps, state, pieces_done, pieces_total}].

    Returns [] when libtorrent is unavailable — the dashboard shows the real
    degraded status from libtorrent_status() instead.
    """
    if _lt is None:
        return []
    return _engine().status_list()


def remove_torrent(tid: str, delete_pieces: bool = True) -> None:
    """Detach a torrent; optionally delete its local piece dir (default True)."""
    _engine().remove(tid, delete_pieces=delete_pieces)
