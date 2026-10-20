"""Stdlib-only resilient-call helpers: exponential backoff + jitter and a
transient/permanent error classifier.

Patterns ported (not dependencies — CloudDog is stdlib-only by repo law):
tenacity's ``wait_random_exponential`` jitter shape and go-retryablehttp's
check-retry + backoff + hook interface. See
agent-ops/research-friction-20261019/01-resilient-transfer.md (W1).

Key finding from that research: ``gh`` does NOT retry transient transfer
failures — its HTTP client is single-attempt. Any wrapper around ``gh``
(or curl/aria2/tbupload subprocesses) must BE the retry layer. This module
is that layer, shared by all backends.
"""
from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class TransientError(Exception):
    """A failure worth retrying (proxy flake, 5xx, connection reset...)."""


class PermanentError(Exception):
    """A failure retrying cannot fix (bad auth, validation, missing file)."""


#: Substrings (lowercased stderr/message text) that mark a failure transient.
TRANSIENT_HINTS = (
    "rate limit",
    "secondary rate",
    "temporarily unavailable",
    "temporarily",
    "timeout",
    "timed out",
    "connection refused",
    "econnrefused",
    "connection reset",
    "connection",
    "network",
    "eof",
    "429",
    "500",
    "502",
    "503",
    "504",
    "socket",
    "context deadline",
    "stream error",
    "stream was cancelled",
    "cancel",
    "empty reply",
    "proxy",
)

#: Substrings that mark a failure permanent — retrying only wastes time.
#: Checked BEFORE transient hints: a "404" must not be retried just because
#: the message also mentions "network".
PERMANENT_HINTS = (
    "404",
    "422",
    "not found",
    "no such file",
    "permission denied",
    "bad credentials",
    "invalid argument",
    "validation failed",
)


def classify_error(
    text: str,
    *,
    transient_hints: tuple[str, ...] = TRANSIENT_HINTS,
    permanent_hints: tuple[str, ...] = PERMANENT_HINTS,
) -> bool:
    """True if the failure described by ``text`` looks transient.

    Permanent hints win over transient ones. A bare ``401`` is permanent
    (real auth loss — fail fast) UNLESS it co-occurs with a transient
    marker (proxy-injected 401s ride along with connection/stream text).
    Unknown errors default to permanent: retrying the unknown forever is
    how lanes hang; callers that know better can raise TransientError.
    """
    low = (text or "").lower()
    if "401" in low or "unauthorized" in low:
        strong_transient = (
            "connection", "stream", "proxy", "timeout", "timed out",
            "temporarily", "reset", "eof",
        )
        return any(h in low for h in strong_transient)
    if any(h in low for h in permanent_hints):
        return False
    return any(h in low for h in transient_hints)


def transient_from_returncode(returncode: int, stderr: str) -> bool:
    """Classify a finished subprocess: rc 0 is never an error; otherwise
    defer to :func:`classify_error` on its stderr."""
    if returncode == 0:
        return False
    return classify_error(stderr)


def with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 8,
    max_elapsed_s: float = 1800.0,
    base_s: float = 2.0,
    cap_s: float = 120.0,
    jitter: bool = True,
    is_transient: Callable[[BaseException], bool] | None = None,
    before_sleep: Callable[[int, float, BaseException], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
) -> T:
    """Call ``fn`` retrying transient failures with exp backoff + jitter.

    Delay for attempt n (0-based) is ``min(cap, base * 2**n)`` with
    ±50% jitter (tenacity's wait_random_exponential shape). Retries stop
    at ``max_attempts`` or when the next sleep would exceed
    ``max_elapsed_s`` — bounded, so a retry storm can't run forever.

    ``fn`` signals retryability by raising :class:`TransientError`;
    :class:`PermanentError` (and anything ``is_transient`` rejects) is
    raised immediately. ``before_sleep(attempt, delay, exc)`` is called
    before each retry sleep (logging hook).
    """
    if is_transient is None:
        is_transient = lambda exc: isinstance(exc, TransientError)  # noqa: E731
    rand = rng if rng is not None else random
    start = clock()
    attempt = 0
    while True:
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised unless transient
            if not is_transient(exc):
                raise
            attempt += 1
            if attempt >= max_attempts:
                raise
            delay = min(cap_s, base_s * (2 ** (attempt - 1)))
            if jitter:
                delay = delay * rand.uniform(0.5, 1.5)
            if clock() - start + delay > max_elapsed_s:
                raise
            if before_sleep is not None:
                before_sleep(attempt, delay, exc)
            sleep(delay)
