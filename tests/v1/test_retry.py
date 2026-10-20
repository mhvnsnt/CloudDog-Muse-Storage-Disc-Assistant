"""Unit tests for retry.py + net.py (resilient-transfer pull-in W1/W4).

Includes simulated proxy flakes: gh-style subprocess failures whose stderr
matches the egress-proxy symptoms (ECONNREFUSED, stream CANCEL, injected
401) must be classified transient and ridden through by with_backoff.
"""
from __future__ import annotations

import random

import pytest

from clouddog import net
from clouddog.retry import (
    PermanentError,
    TransientError,
    classify_error,
    transient_from_returncode,
    with_backoff,
)


# ------------------------------------------------------------------ #
# classifier
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("text", [
    "dial tcp: connect: connection refused",
    "Post https://uploads.github.com: EOF",
    "HTTP 503 Service Unavailable",
    "error: 429 Too Many Requests",
    "stream error: stream ID 7; CANCEL",
    "empty reply from server",
    "context deadline exceeded",
    "i/o timeout",
    "502 Bad Gateway via proxy",
])
def test_transient_texts(text):
    assert classify_error(text) is True


@pytest.mark.parametrize("text", [
    "404 Not Found",
    "422 Unprocessable Entity: validation failed",
    "open /nope: no such file or directory",
    "permission denied",
])
def test_permanent_texts(text):
    assert classify_error(text) is False


def test_bare_401_is_permanent_but_proxy_401_is_transient():
    assert classify_error("401 Unauthorized: bad credentials") is False
    assert classify_error("401 Unauthorized (connection reset by proxy)") is True


def test_returncode_zero_never_transient():
    assert transient_from_returncode(0, "connection refused") is False
    assert transient_from_returncode(1, "connection refused") is True


# ------------------------------------------------------------------ #
# with_backoff
# ------------------------------------------------------------------ #
def test_backoff_rides_through_proxy_flakes():
    """Simulated proxy flake: fail transiently 3x, then succeed."""
    calls = {"n": 0}
    sleeps: list[float] = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 4:
            raise TransientError("connection refused")
        return "ok"

    out = with_backoff(flaky, sleep=sleeps.append, rng=random.Random(7))
    assert out == "ok"
    assert calls["n"] == 4
    assert len(sleeps) == 3
    # exponential shape: each nominal delay doubles (2, 4, 8) ±50% jitter
    for delay, nominal in zip(sleeps, (2.0, 4.0, 8.0)):
        assert nominal * 0.5 <= delay <= nominal * 1.5


def test_backoff_gives_up_at_max_attempts():
    calls = {"n": 0}

    def always():
        calls["n"] += 1
        raise TransientError("503")

    with pytest.raises(TransientError):
        with_backoff(always, max_attempts=3, sleep=lambda s: None)
    assert calls["n"] == 3


def test_backoff_never_retries_permanent():
    def perm():
        raise PermanentError("404")

    with pytest.raises(PermanentError):
        with_backoff(perm, sleep=lambda s: None)


def test_backoff_respects_max_elapsed():
    ticks = iter([0.0, 100.0, 200.0, 10_000.0])

    def always():
        raise TransientError("timeout")

    with pytest.raises(TransientError):
        with_backoff(always, max_elapsed_s=60.0, clock=lambda: next(ticks),
                     sleep=lambda s: None)


def test_before_sleep_hook_fires():
    seen = []
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] < 3:
            raise TransientError("eof")
        return 1

    with_backoff(fn, sleep=lambda s: None,
                 before_sleep=lambda a, d, e: seen.append(a))
    assert seen == [1, 2]


# ------------------------------------------------------------------ #
# proxy env sanitizer (TOOLS.md no_proxy IPv6 quirk)
# ------------------------------------------------------------------ #
def test_proxy_env_strips_ipv6_literals():
    base = {
        "no_proxy": "localhost,::1,[::1],127.0.0.1,[::1]:8080,10.0.0.0/8",
        "NO_PROXY": "::1,example.com",
        "https_proxy": "http://user:secret@proxy.local:3128",
    }
    env = net.proxy_env(base)
    assert env["no_proxy"] == "localhost,127.0.0.1,10.0.0.0/8"
    assert env["NO_PROXY"] == "example.com"
    # proxy vars themselves pass through untouched
    assert env["https_proxy"].endswith("proxy.local:3128")


def test_proxy_env_bypass_escape_hatch():
    base = {"https_proxy": "http://p:1", "CLOUDDOG_NO_PROXY": "*"}
    env = net.proxy_env(base)
    assert "https_proxy" not in env
    assert env["no_proxy"] == "*"


def test_proxy_endpoint_redacts_credentials():
    env = {"https_proxy": "http://user:secret@proxy.local:3128"}
    assert net.proxy_endpoint(env) == "proxy.local:3128"
    assert "secret" not in net.proxy_endpoint(env)
    assert net.proxy_endpoint({}) == "direct"


# ------------------------------------------------------------------ #
# flag presets
# ------------------------------------------------------------------ #
def test_flag_presets():
    curl = net.preset("curl")
    assert "--retry-connrefused" in curl and "--retry-all-errors" in curl
    aria2 = net.preset("aria2")
    assert "--always-resume=true" in aria2 and "--max-tries=0" in aria2
    rclone = net.preset("rclone")
    assert "--low-level-retries" in rclone
    with pytest.raises(ValueError):
        net.preset("wget")



# ------------------------------------------------------------------ #
# github backend wiring: _run_gh retries via with_backoff and passes
# the sanitized proxy env to subprocess
# ------------------------------------------------------------------ #
def test_run_gh_retries_proxy_flake_and_sanitizes_env(monkeypatch):
    import subprocess as sp

    from clouddog.stores import github as gh_mod

    monkeypatch.setenv("no_proxy", "localhost,::1,[::1]")
    seen_envs = []
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        seen_envs.append(kwargs.get("env"))
        if calls["n"] < 3:  # two proxy flakes, then success
            return sp.CompletedProcess(cmd, 1, "", "dial tcp: connection refused")
        return sp.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    backend = gh_mod.GitHubReleaseBackend()
    proc = backend._run_gh(["release", "view", "v1"])
    assert proc.returncode == 0
    assert calls["n"] == 3
    assert seen_envs[0]["no_proxy"] == "localhost"


def test_run_gh_permanent_failure_fails_fast(monkeypatch):
    import subprocess as sp

    from clouddog.stores import github as gh_mod
    from clouddog.errors import CloudDogError

    calls = {"n": 0}

    def fake_run(cmd, **kwargs):
        calls["n"] += 1
        return sp.CompletedProcess(cmd, 1, "", "404 Not Found")

    monkeypatch.setattr(gh_mod.subprocess, "run", fake_run)
    backend = gh_mod.GitHubReleaseBackend()
    with pytest.raises(CloudDogError):
        backend._run_gh(["release", "view", "v1"])
    assert calls["n"] == 1
