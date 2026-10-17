"""CloudDog storage backends.

A Store is a permanent home for offloaded files. Local disk is a cache:
files are mirrored to a Store, byte-verified, and only then cleared locally.

Concrete backends live in sibling modules and are registered in REGISTRY.
The GitHub/TeraBox/Kaggle backends live in the ``cloud_dog.py`` engine
(the v2 lane owns them); this package contributes the backends v2 does
not have — currently Drive (via rclone).
"""

from __future__ import annotations

import hashlib
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class StoredRef:
    """Pointer to one object in a permanent store."""

    store: str
    remote_path: str
    size: int
    sha256: str
    uri: str


class StoreError(Exception):
    """Base error for all store operations."""


class StoreUnavailable(StoreError):
    """The store cannot be used (missing config, credentials, or tooling)."""


class VerificationFailed(StoreError):
    """The object was written but did not verify on the remote."""


# Best-effort redaction of token/cookie-looking values in error text.
_TOKEN_VALUE_RE = re.compile(
    r"(?P<k>[\"']?(?:jstoken|ndus|ndut_fmt|csrftoken|csftoken|browserid"
    r"|sid|bdstoken|stoken|cookie|cookies|token|secret|password|api[-_]?key"
    r"|access[-_]?key)[\"']?\s*[:=]\s*)(?P<v>[\"']?[^\"'\s;,&}]+)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_\-.~+/=]+")


def _redact(text: str) -> str:
    """Redact anything that looks like a token/cookie value in an error string."""
    red = _TOKEN_VALUE_RE.sub(lambda m: m.group("k") + "***", text)
    return _BEARER_RE.sub(lambda m: m.group(1) + "***", red)


def run_with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 5,
    base_delay: float = 2.0,
    what: str = "operation",
) -> T:
    """Run ``fn`` with exponential backoff (base, 2*base, 4*base, ...).

    Reraises the last exception after ``attempts`` failures.
    """
    last: BaseException | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retries are the point; last is reraised
            last = exc
            if attempt < attempts - 1:
                time.sleep(base_delay * (2**attempt))
    assert last is not None
    raise last


def sha256_of(path: str) -> str:
    """SHA-256 hex digest of a file, streamed so large files stay in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Store(ABC):
    """Abstract permanent store backend.

    ``config`` is the ``stores.<name>`` dict from the CloudDog config file.
    Missing required keys raise StoreUnavailable with a clear message.
    """

    name: ClassVar[str] = "store"

    def __init__(self, config: dict) -> None:
        self.config = dict(config)

    @abstractmethod
    def upload(
        self,
        local_path: str,
        remote_dir: str = "",
        remote_name: str | None = None,
    ) -> StoredRef:
        """Upload a local file and return a verified StoredRef."""
        ...

    @abstractmethod
    def verify(self, ref: StoredRef) -> bool:
        """Re-check the remote NOW and compare against ``ref``.

        Returns True/False. Raises StoreError only on transport failure.
        """
        ...

    @abstractmethod
    def exists(self, remote_dir: str, remote_name: str) -> StoredRef | None:
        """Return a StoredRef for an already-stored object, or None."""
        ...

    def uri_for(self, ref: StoredRef) -> str:
        """Canonical URI for a stored reference."""
        return f"{self.name}:{ref.remote_path}"


from .drive import DriveStore

REGISTRY: dict[str, type[Store]] = {
    "drive": DriveStore,
}


def get_store(name: str, stores_config: dict) -> Store:
    """Instantiate the store named ``name`` from the CloudDog stores config."""
    try:
        cls = REGISTRY[name]
    except KeyError:
        valid = ", ".join(sorted(REGISTRY))
        raise StoreError(f"Unknown store {name!r}. Valid stores: {valid}") from None
    return cls(stores_config.get(name, {}))
