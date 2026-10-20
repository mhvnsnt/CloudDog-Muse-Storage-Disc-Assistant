"""Earned autonomy — CloudDog's persisted trust state.

Owner law (2026-10-20): CloudDog's feedback used to reward DELETION
(bytes freed). That is backwards. The daemon must be FED for correct
behavior and STARVED (privileges revoked) for violations. This module
is the starvation/feeding ledger: a small JSON state file
(``~/.clouddog/trust.json`` by default) that every delete-capable lane
consults before it may run with ``--apply``.

States, in earning order:

  REVOKED   — dry-run only. The state CloudDog starts in, and the state
              any VIOLATION or NEAR-MISS anywhere in the system drops it
              back to, on the spot (persisted immediately).
  PROBATION — dry-run, earning. Clean cycles accumulate a streak here.
  APPLY     — full autonomy. Reached only after ``required_streak``
              consecutive clean cycles (zero violations, zero
              near-misses, zero unmirrored-old findings).

A cycle containing UNMIRRORED_OLD findings is not clean (the streak
resets) but does not by itself revoke — revocation is reserved for
gate-evidence of a bad delete intent (VIOLATION / NEAR-MISS), per the
owner's wording.

First-run initialization is REVOKED with the owner's revocation reason
recorded verbatim, so a fresh install can never silently start with
apply privileges.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

__all__ = [
    "APPLY",
    "PROBATION",
    "REVOKED",
    "STATES",
    "DEFAULT_REQUIRED_STREAK",
    "INITIAL_REASON",
    "default_trust_path",
    "load_trust",
    "save_trust",
    "current_state",
    "apply_allowed",
    "revoke",
    "record_clean_cycle",
    "record_unclean_cycle",
]

REVOKED = "REVOKED"
PROBATION = "PROBATION"
APPLY = "APPLY"
STATES = (REVOKED, PROBATION, APPLY)

#: Consecutive clean cycles required to earn APPLY from PROBATION.
#: Configurable per trust file (``required_streak``) or via env.
DEFAULT_REQUIRED_STREAK = 20
ENV_TRUST_PATH = "CLOUDEDOG_TRUST_PATH"
ENV_TRUST_STREAK = "CLOUDEDOG_TRUST_REQUIRED_STREAK"

#: Why a fresh trust file starts REVOKED (owner revocation, 2026-10-20).
INITIAL_REASON = (
    "owner revocation 2026-10-20 — pre-trust-layer violations "
    "(tripo-wire, 4-blend casualty)"
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_trust_path() -> str:
    return os.environ.get(ENV_TRUST_PATH) or os.path.expanduser(
        "~/.clouddog/trust.json"
    )


def _default_required_streak() -> int:
    try:
        return max(1, int(os.environ.get(ENV_TRUST_STREAK) or DEFAULT_REQUIRED_STREAK))
    except (TypeError, ValueError):
        return DEFAULT_REQUIRED_STREAK


def _fresh_state(required_streak: int | None = None) -> dict:
    return {
        "state": REVOKED,
        "clean_streak": 0,
        "required_streak": int(required_streak or _default_required_streak()),
        "reason": INITIAL_REASON,
        "updated_at": _utcnow(),
        "revoked_at": _utcnow(),
        "last_cycle_at": "",
        "last_cycle_source": "",
        "total_clean_cycles": 0,
        "total_revocations": 0,
    }


def save_trust(state: dict, path: str | None = None) -> dict:
    """Persist the trust state atomically (tmp + replace + fsync)."""
    path = path or default_trust_path()
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    state = dict(state)
    state["updated_at"] = _utcnow()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return state


def load_trust(path: str | None = None, required_streak: int | None = None) -> dict:
    """Load the trust state, initializing (and persisting) REVOKED on
    first run. A corrupt/unreadable file fails CLOSED to REVOKED."""
    path = path or default_trust_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return save_trust(_fresh_state(required_streak), path)
    except (OSError, ValueError):
        state = _fresh_state(required_streak)
        state["reason"] = "trust file unreadable/corrupt; failing closed to REVOKED"
        try:
            return save_trust(state, path)
        except OSError:
            return state
    if not isinstance(data, dict) or data.get("state") not in STATES:
        state = _fresh_state(required_streak)
        state["reason"] = "trust file malformed; failing closed to REVOKED"
        try:
            return save_trust(state, path)
        except OSError:
            return state
    data.setdefault("clean_streak", 0)
    data.setdefault("required_streak", int(required_streak or _default_required_streak()))
    if required_streak is not None:
        data["required_streak"] = int(required_streak)
    data.setdefault("reason", "")
    data.setdefault("total_clean_cycles", 0)
    data.setdefault("total_revocations", 0)
    return data


def current_state(path: str | None = None) -> str:
    return str(load_trust(path).get("state") or REVOKED)


def apply_allowed(path: str | None = None) -> bool:
    """True only in the APPLY state — the daemon's --apply gate."""
    return current_state(path) == APPLY


def revoke(path: str | None = None, reason: str = "") -> dict:
    """Drop to REVOKED on the spot and persist immediately.

    Called the moment a VIOLATION or NEAR-MISS is graded anywhere in
    the system. The streak is wiped: autonomy is re-earned from zero.
    """
    state = load_trust(path)
    state["state"] = REVOKED
    state["clean_streak"] = 0
    state["reason"] = reason or "revoked: violation or near-miss graded"
    state["revoked_at"] = _utcnow()
    state["total_revocations"] = int(state.get("total_revocations") or 0) + 1
    return save_trust(state, path)


def record_clean_cycle(path: str | None = None, source: str = "") -> dict:
    """Feed one clean cycle (zero violations, zero near-misses, zero
    unmirrored-old findings).

    REVOKED -> PROBATION on the first clean cycle (earning begins);
    PROBATION -> APPLY once the streak reaches ``required_streak``;
    APPLY stays APPLY.
    """
    state = load_trust(path)
    streak = int(state.get("clean_streak") or 0) + 1
    state["clean_streak"] = streak
    state["total_clean_cycles"] = int(state.get("total_clean_cycles") or 0) + 1
    state["last_cycle_at"] = _utcnow()
    state["last_cycle_source"] = source
    required = int(state.get("required_streak") or _default_required_streak())
    if state.get("state") == REVOKED:
        state["state"] = PROBATION
        state["reason"] = "earning: clean cycles under probation (dry-run only)"
    if state.get("state") == PROBATION and streak >= required:
        state["state"] = APPLY
        state["reason"] = (
            f"earned: {streak} consecutive clean cycles "
            f"(required {required})"
        )
    return save_trust(state, path)


def record_unclean_cycle(path: str | None = None, source: str = "",
                         reason: str = "") -> dict:
    """A cycle that is not clean but carries no VIOLATION/NEAR-MISS
    (e.g. UNMIRRORED_OLD findings): the promotion streak resets, the
    state itself is unchanged (revocation is handled by :func:`revoke`
    at grade time for violations/near-misses)."""
    state = load_trust(path)
    state["clean_streak"] = 0
    state["last_cycle_at"] = _utcnow()
    state["last_cycle_source"] = source
    if reason:
        state["reason"] = reason
    return save_trust(state, path)
