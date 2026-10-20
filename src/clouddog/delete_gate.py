"""The delete gate — CloudDog's hard no-unlink invariant.

INCIDENT (2026-10-20 ~02:52Z): the CloudDog daemon, restarted with
--apply by the keepalive cron, deleted ~/workspace/tripo-wire-20261019
(79 files, 1,068,247,040 B) with NO store destination and NO registry
entry. Root cause: the daemon's --apply path (Daemon._process_entry ->
Daemon._delete_local) was a parallel delete authority that never
consulted the registry, never checked work claims, and never routed
through the law engine — unlike cmd_reap / cmd_reap_v2, which can only
ever delete registry entries that pass claims + live verify. A file
with no registry entry was fully eligible for daemon deletion.

This module is the fix's choke point. EVERY code path that unlinks a
local file after a store (daemon, reaper, streamer) must pass through
:func:`check_delete_gate` at delete time, and may unlink only when the
verdict allows it. ALL four legs must hold at delete time:

  (a) registry — a registry entry exists for the path, is not already
      marked reaped, and BINDS to the local bytes (the entry's recorded
      sha256/digest and size match the file as it stands right now);
  (b) verify — the entry's store copy was LIVE-verified within this
      run (hash+size against the store itself, not a cached flag);
  (c) claims — no live work claim covers the path, and the claims
      store was actually readable (an unreadable store, or no checker
      wired at all, fails CLOSED);
  (d) policy — a fresh policy.evaluate() at delete time does not
      return SACRED / KEEP_LOCAL (callers pass their deletable-action
      set; SACRED blocks unconditionally, even if a caller whitelists
      it by mistake).

Any missing piece = fail closed. Callers journal every block with the
per-leg reasons this module returns, so a blocked delete is never
silent (the incident delete left no journal trace at all).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GateBlock", "GateVerdict", "check_delete_gate"]


@dataclass(frozen=True)
class GateBlock:
    """One failing leg of the delete gate."""

    leg: str  # "registry" | "verify" | "claims" | "policy"
    reason: str


@dataclass(frozen=True)
class GateVerdict:
    """Outcome of a delete-gate check. ``allowed`` is True only when
    ``blocks`` is empty — every leg held at delete time."""

    allowed: bool
    blocks: tuple[GateBlock, ...] = ()

    @property
    def summary(self) -> str:
        """One-line, journal-ready description of the verdict."""
        if self.allowed:
            return "delete gate: all legs hold (registry, live verify, claims, policy)"
        return "delete gate BLOCKED: " + "; ".join(
            f"[{b.leg}] {b.reason}" for b in self.blocks
        )


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def check_delete_gate(
    path: str,
    *,
    registry_entry: dict | None,
    local_sha256: str = "",
    local_size: int | None = None,
    local_digest: str = "",
    local_digest_algo: str = "",
    live_verify_ok: bool = False,
    claim_check=None,
    policy_action: str = "",
    deletable_actions=frozenset({"store_and_clear"}),
) -> GateVerdict:
    """Evaluate the hard delete invariant for ``path``.

    Parameters are the leg inputs, gathered by the caller at delete
    time (never cached from an earlier pass):

    - ``registry_entry``: the registry record for the path, or None.
      Binding is checked against ``local_sha256``/``local_size`` when
      the entry records a sha256, else against ``local_digest`` (with
      ``local_digest_algo``, e.g. the reaper's git-sha1 manifest hash).
    - ``live_verify_ok``: True only when the caller live-verified the
      ENTRY's store copy during this run (re-download / live store
      check — a stored flag from a previous run does not count).
    - ``claim_check``: callable(path) -> (blocked, detail), e.g.
      ``clouddog.claims.make_claim_checker()``. None fails closed.
    - ``policy_action``: the fresh ``policy.evaluate()`` action value
      for the path at delete time; ``deletable_actions`` is the
      caller's set of actions that may proceed to unlink.
    """
    blocks: list[GateBlock] = []

    # ---- (a) registry: entry exists and binds to the local bytes ----
    if registry_entry is None:
        blocks.append(
            GateBlock("registry", f"no registry entry for {path}; refusing to delete unregistered work")
        )
    else:
        status = str(registry_entry.get("status") or "registered")
        if status == "reaped":
            blocks.append(
                GateBlock("registry", "registry entry already marked reaped; refusing a second delete")
            )
        entry_sha = str(registry_entry.get("sha256") or "")
        entry_digest = str(registry_entry.get("digest") or "")
        entry_algo = str(registry_entry.get("digest_algo") or "")
        if entry_sha:
            if not local_sha256:
                blocks.append(
                    GateBlock("registry", "registry entry records a sha256 but no local sha256 was supplied to bind against")
                )
            elif entry_sha.lower() != str(local_sha256).lower():
                blocks.append(
                    GateBlock("registry", "registry entry sha256 does not match the local file's current sha256")
                )
        elif entry_digest:
            if not local_digest or (entry_algo and local_digest_algo and entry_algo != local_digest_algo):
                blocks.append(
                    GateBlock("registry", "registry entry digest cannot be bound to the local file (missing or mismatched digest)")
                )
            elif entry_digest.lower() != str(local_digest).lower():
                blocks.append(
                    GateBlock("registry", "registry entry digest does not match the local file's current digest")
                )
        else:
            blocks.append(
                GateBlock("registry", "registry entry records no sha256/digest; cannot bind the entry to the local bytes")
            )
        entry_size = _int_or_none(registry_entry.get("size"))
        if entry_size is None:
            blocks.append(
                GateBlock("registry", "registry entry records no size; cannot bind the entry to the local bytes")
            )
        elif local_size is not None and entry_size != int(local_size):
            blocks.append(
                GateBlock("registry", "registry entry size does not match the local file's current size")
            )

    # ---- (b) live verify within this run ----------------------------
    if not live_verify_ok:
        blocks.append(
            GateBlock("verify", "store copy was not live-verified within this run (hash+size against the store)")
        )

    # ---- (c) claims --------------------------------------------------
    if claim_check is None:
        blocks.append(
            GateBlock("claims", "no claims checker wired; failing closed")
        )
    else:
        try:
            claimed, detail = claim_check(path)
        except Exception as exc:  # noqa: BLE001 - any failure blocks
            blocks.append(
                GateBlock("claims", f"claims check raised ({exc}); failing closed")
            )
        else:
            if claimed:
                blocks.append(
                    GateBlock("claims", f"active work claim: {detail or 'claimed'}")
                )

    # ---- (d) policy at delete time -----------------------------------
    action = str(policy_action or "").lower()
    if action == "sacred":
        # Unconditional: no caller may whitelist SACRED, ever.
        blocks.append(
            GateBlock("policy", "policy evaluate() = SACRED at delete time (in-flight or protected)")
        )
    elif action not in {str(a).lower() for a in deletable_actions}:
        blocks.append(
            GateBlock(
                "policy",
                f"policy evaluate() = {action or 'unknown'} at delete time; "
                f"not in the deletable set {sorted(deletable_actions)}",
            )
        )

    return GateVerdict(allowed=not blocks, blocks=tuple(blocks))
