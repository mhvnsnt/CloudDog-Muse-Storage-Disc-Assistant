"""Abstract store backend contract.

Every backend (GitHub releases, TeraBox, Drive) implements this. Backends
must NEVER fake success: when credentials/tools are absent they report
DEGRADED (or DOWN) from health() and raise StoreDegraded on use.

remote_ref semantics per backend:
  github  -> "owner/repo@tag"            (asset name defaults to basename)
  terabox -> "/remote/dir"               (remote directory path)
  drive   -> "remote:path"               (rclone remote + path)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

#: THE single chunk-size standard for every CloudDog chunking path
#: (owner decision 2026-10-19: "100MB is THE single standard for store()
#: and mirror everywhere — keep behind config, revisit if evidence
#: changes"). 100 MiB. Every backend split, the mirror part stream, and
#: the straight-to-store streamer MUST read this constant — never a
#: hardcoded local size. To change the standard, change it HERE only.
#:
#: NOTE for the straight-to-store streamer lane: when streamer.py lands
#: on main, its part size must be ``CHUNK_BYTES`` from this module (the
#: v1 mirror in cloud_dog.py already derives its --part-mb default from
#: the mirrored PART_BYTES constant there).
CHUNK_BYTES = 100 * 1024 * 1024


class Health(Enum):
    LIVE = "live"
    DEGRADED = "degraded"
    DOWN = "down"


@dataclass
class HealthStatus:
    health: Health
    reason: str = ""


@dataclass
class StoredRef:
    """Serializable reference to a stored object."""

    backend: str
    locator: str  # backend-specific locator (see module docstring)
    size: int
    sha256: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "backend": self.backend,
            "locator": self.locator,
            "size": self.size,
            "sha256": self.sha256,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StoredRef":
        return cls(
            backend=d["backend"],
            locator=d["locator"],
            size=d["size"],
            sha256=d["sha256"],
            extra=d.get("extra", {}),
        )


class StoreBackend(ABC):
    """Contract every store backend implements."""

    name: str = "base"

    @property
    def supports_content_verify(self) -> bool:
        """True when verify() proves byte-identity (sha256), not just
        name+size. Backends that cannot (e.g. TeraBox, whose API exposes
        no remote checksum) MUST override to False; auto-delete paths
        refuse to clear the local copy for such tiers."""

        return True

    @abstractmethod
    def health(self) -> HealthStatus:
        """Probe backend usability. Must not fake success."""

    @abstractmethod
    def store(self, local_path: str, remote_ref: str, dry_run: bool = False) -> StoredRef:
        """Upload local_path to remote_ref. Returns a StoredRef.

        Must verify byte-exactness before returning (size + sha256).
        dry_run=True performs no upload and returns a StoredRef with
        sha256 of the local file and locator marked dry-run.
        """

    @abstractmethod
    def verify(self, local_path: str, stored: StoredRef) -> bool:
        """Re-verify a local file against an existing StoredRef."""
