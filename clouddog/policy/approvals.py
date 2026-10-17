"""Final human gate before any destructive action runs."""

from __future__ import annotations


class ApprovalRequired(Exception):
    """Raised when a destructive action needs explicit --yes approval."""


def require_execute(dry_run: bool, assume_yes: bool, what: str) -> None:
    """Enforce the last approval gate before a destructive action.

    Dry runs always pass silently (the CLI prints the plan instead).
    Otherwise, without ``assume_yes`` (the ``--yes`` flag), raise
    :class:`ApprovalRequired`.
    """
    if dry_run:
        return
    if not assume_yes:
        raise ApprovalRequired(f"Refusing destructive action without --yes: {what}")
