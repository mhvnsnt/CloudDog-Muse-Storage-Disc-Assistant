"""CloudDog: policy engine, Drive store, config, and registry bridge.

This package EXTENDS the ``cloud_dog.py`` engine (the v2 lane owns the
GitHub/TeraBox/Kaggle backends, the CLI, daemon mode, mirror mode, and the
audit journal). What this package adds:

- :mod:`clouddog.policy` — the owner's disk laws as code: protected
  game-file matching, in-progress transfer detection, the ONE-STORE
  decision engine, and approval gates.
- :mod:`clouddog.stores` — the importable ``Store`` interface plus the
  backends v2 does not have (Drive via rclone).
- :mod:`clouddog.config` — validated YAML configuration.
- :mod:`clouddog.bridge` — fold ``cloud_dog.py``'s registry into the v2
  audit journal so there is one audit trail.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
