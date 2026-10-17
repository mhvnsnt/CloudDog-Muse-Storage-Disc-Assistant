"""Configuration loading and defaults for CloudDog."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml


class ConfigError(Exception):
    """Raised when a config file cannot be parsed or has invalid values."""


DEFAULT_CONFIG_PATH = "~/.clouddog/config.yaml"


@dataclass
class Config:
    """CloudDog configuration."""

    stores: dict = field(default_factory=dict)
    thresholds: dict = field(
        default_factory=lambda: {"warn_pct": 75, "target_pct": 70, "ceiling_pct": 88}
    )
    scan_roots: list[str] = field(default_factory=lambda: ["/home/hatch/workspace"])
    protected_extra: list[str] = field(default_factory=list)
    strict_verify: bool = False
    journal_path: str = "~/.clouddog/journal.jsonl"
    terabox_remote_root: str = "/CN-Gigaleak"
    github_repo: str = ""
    github_tag: str = ""
    state_dir: str = "~/.clouddog"


def default_config() -> Config:
    """Return a Config with default values."""
    return Config()


def _check_type(cfg_dict: dict, key: str, value: object, expected: type | tuple) -> None:
    if not isinstance(value, expected):
        names = (
            " | ".join(t.__name__ for t in expected)
            if isinstance(expected, tuple)
            else expected.__name__
        )
        raise ConfigError(
            f"Invalid config key '{key}': expected {names}, got {type(value).__name__}"
        )


def load_config(path: str | None = None) -> Config:
    """Load configuration from a YAML file.

    A missing file returns :func:`default_config` (not an error).
    Unparseable YAML or wrong types raise :class:`ConfigError`
    naming the bad key.
    """
    path = path or DEFAULT_CONFIG_PATH
    expanded = os.path.expanduser(path)
    if not os.path.exists(expanded):
        return default_config()
    try:
        with open(expanded, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in config file '{expanded}': {exc}") from exc
    if data is None:
        return default_config()
    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid config file '{expanded}': top level must be a mapping, "
            f"got {type(data).__name__}"
        )

    known = {
        "stores": dict,
        "thresholds": dict,
        "scan_roots": list,
        "protected_extra": list,
        "strict_verify": bool,
        "journal_path": str,
        "terabox_remote_root": str,
        "github_repo": str,
        "github_tag": str,
        "state_dir": str,
    }
    kwargs: dict = {}
    for key, value in data.items():
        if key not in known:
            raise ConfigError(f"Unknown config key '{key}' in '{expanded}'")
        _check_type(data, key, value, known[key])
        kwargs[key] = value

    if "thresholds" in kwargs:
        for sub in ("warn_pct", "target_pct", "ceiling_pct"):
            if sub in kwargs["thresholds"] and not isinstance(
                kwargs["thresholds"][sub], (int, float)
            ):
                raise ConfigError(
                    f"Invalid config key 'thresholds.{sub}': expected number, "
                    f"got {type(kwargs['thresholds'][sub]).__name__}"
                )
    return Config(**kwargs)


def write_default_config(path: str) -> None:
    """Write a commented default config YAML, creating parent dirs."""
    expanded = os.path.expanduser(path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    content = (
        "# CloudDog configuration. Local disk is a cache, not storage.\n"
        "# Every delete is journaled and gated by policy (clouddog.policy).\n"
        "\n"
        "# Permanent stores to mirror to. Keys are store names used by the CLI;\n"
        "# each value holds the store-specific settings (see clouddog.stores).\n"
        "stores: {}\n"
        "\n"
        "# Disk usage thresholds (percent of disk used).\n"
        "thresholds:\n"
        "  warn_pct: 75     # warn at this usage\n"
        "  target_pct: 70   # sweep down to this\n"
        "  ceiling_pct: 88  # emergency sweep at this\n"
        "\n"
        "# Roots scanned for sweep candidates.\n"
        'scan_roots:\n  - "/home/hatch/workspace"\n'
        "\n"
        "# Extra fnmatch patterns treated as protected (in addition to policy defaults).\n"
        "protected_extra: []\n"
        "\n"
        "# When true, verification requires byte-identical hashes on every\n"
        "# named store instead of the ONE-STORE RULE minimum.\n"
        "strict_verify: false\n"
        "\n"
        "# Append-only audit journal: every delete/store/verify gets an entry.\n"
        'journal_path: "~/.clouddog/journal.jsonl"\n'
        "\n"
        "# Remote roots for the stores.\n"
        'terabox_remote_root: "/CN-Gigaleak"\n'
        'github_repo: ""\n'
        'github_tag: ""\n'
        "\n"
        "# Local state directory.\n"
        'state_dir: "~/.clouddog"\n'
    )
    with open(expanded, "w", encoding="utf-8") as fh:
        fh.write(content)
