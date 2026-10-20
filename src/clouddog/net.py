"""Network-environment hygiene + transfer-tool flag presets.

``_proxy_env()`` fixes the no_proxy IPv6-literal quirk documented in
~/TOOLS.md: the egress environment's ``no_proxy``/``NO_PROXY`` contain bare
(``::1``) and bracketed (``[::1]``) IPv6 literals, which crash httpx's URL
parser ("Invalid port: ':1]'") and confuse other clients. Every subprocess
that talks to the network (gh, curl, aria2c, tbupload) should receive the
sanitized env from here instead of inheriting ``os.environ`` raw.

The flag presets are the resilient-transfer bundles from
agent-ops/research-friction-20261019/01-resilient-transfer.md (W3/W6):
segmented, resumable, retry-heavy flag sets lanes can call by name.
``gh`` has no retry flags at all — that retry layer lives in retry.py.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

#: Set CLOUDDOG_NO_PROXY='*' to bypass the proxy entirely for a lane
#: (curl --noproxy '*' semantics) when the proxy itself is the failure.
BYPASS_ENV = "CLOUDDOG_NO_PROXY"


def _strip_ipv6_literals(value: str) -> str:
    """Drop comma-separated no_proxy entries that are IPv6 literals.

    An entry is an IPv6 literal if it contains ``::`` (bare, bracketed,
    or with a port). Hostnames and IPv4/CIDR entries pass through.
    """
    kept = []
    for entry in (value or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "::" in entry:
            continue
        kept.append(entry)
    return ",".join(kept)


def _redacted_proxy(url: str) -> str:
    """host:port of a proxy URL with credentials stripped, for logging."""
    if not url:
        return ""
    try:
        parts = urlsplit(url if "://" in url else f"//{url}")
        host = parts.hostname or ""
        return f"{host}:{parts.port}" if parts.port else host
    except ValueError:
        return "<unparsable>"


def proxy_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of the environment safe to hand to network tools.

    - Strips IPv6 literals from ``no_proxy``/``NO_PROXY`` (httpx quirk).
    - ``CLOUDDOG_NO_PROXY='*'`` in the base env empties all proxy vars so
      the call bypasses the proxy entirely.
    """
    env = dict(os.environ if base is None else base)
    if env.get(BYPASS_ENV) == "*":
        for key in (
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
        ):
            env.pop(key, None)
        env["no_proxy"] = "*"
        env["NO_PROXY"] = "*"
        return env
    for key in ("no_proxy", "NO_PROXY"):
        if key in env:
            env[key] = _strip_ipv6_literals(env[key])
    return env


def proxy_endpoint(env: dict[str, str] | None = None) -> str:
    """Redacted host:port of the effective proxy, for attempt logging."""
    env = os.environ if env is None else env
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        if env.get(key):
            return _redacted_proxy(env[key])
    return "direct"


# --------------------------------------------------------------------- #
# Flag presets (research doc 01, W3/W6). Named so lanes call by name.
# --------------------------------------------------------------------- #

#: curl: retry everything retryable incl. connection-refused (our exact
#: proxy symptom), kill silent stalls via speed floor, box total time.
CURL_RESILIENT: tuple[str, ...] = (
    "--retry",
    "8",
    "--retry-all-errors",
    "--retry-connrefused",
    "--retry-delay",
    "5",
    "--retry-max-time",
    "1800",
    "--speed-limit",
    "10000",
    "--speed-time",
    "60",
    "--connect-timeout",
    "30",
)

#: aria2 download: segmented, resumable via .aria2 control file, retries
#: within the lane (the lane's own outer timeout still governs — the
#: NEVER-KILL-IN-PROGRESS law is about lanes, not in-lane retries).
ARIA2_RESILIENT: tuple[str, ...] = (
    "--continue=true",
    "--always-resume=true",
    "--max-tries=0",
    "--retry-wait=10",
    "--auto-save-interval=30",
    "--max-resume-failure-tries=10",
    "--allow-overwrite=false",
    "--conditional-get=true",
    "--connect-timeout=30",
    "--timeout=600",
    "--max-connection-per-server=8",
    "--split=8",
    "--min-split-size=10M",
    "--file-allocation=none",
)

#: rclone: two-tier retry (operation + low-level HTTP), generous timeouts.
RCLONE_RESILIENT: tuple[str, ...] = (
    "--retries",
    "10",
    "--retries-sleep",
    "30s",
    "--low-level-retries",
    "20",
    "--timeout",
    "300s",
    "--contimeout",
    "60s",
)

FLAG_PRESETS: dict[str, tuple[str, ...]] = {
    "curl": CURL_RESILIENT,
    "aria2": ARIA2_RESILIENT,
    "rclone": RCLONE_RESILIENT,
}


def preset(tool: str) -> list[str]:
    """Flag list for a named preset ('curl' | 'aria2' | 'rclone')."""
    try:
        return list(FLAG_PRESETS[tool])
    except KeyError:
        raise ValueError(f"unknown flag preset {tool!r}; have {sorted(FLAG_PRESETS)}") from None


_PROXY_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+(:\d+)?$")
