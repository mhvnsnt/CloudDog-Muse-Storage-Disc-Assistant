"""CloudDog: disk-as-cache storage assistant.

Local disk is a cache, not storage. CloudDog continuously mirrors files to
permanent stores (TeraBox, GitHub releases, Google Drive), byte-verifies
them, then clears local disk. The owner's standing disk laws are encoded
as code in :mod:`clouddog.policy`.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
