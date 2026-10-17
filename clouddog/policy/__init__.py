"""Policy layer: the owner's disk laws as code plus approval gates."""

from clouddog.policy.approvals import ApprovalRequired, require_execute
from clouddog.policy.laws import (
    ARCHIVE_SUFFIXES,
    DO_NOT_DELETE_MARKER,
    IN_PROGRESS_DIR_HINTS,
    IN_PROGRESS_SUFFIXES,
    PROTECTED_PATH_PARTS,
    PROTECTED_SUFFIXES,
    Decision,
    decide_delete,
    has_do_not_delete_marker,
    is_in_progress,
    is_protected_game_file,
)

__all__ = [
    "ARCHIVE_SUFFIXES",
    "DO_NOT_DELETE_MARKER",
    "IN_PROGRESS_DIR_HINTS",
    "IN_PROGRESS_SUFFIXES",
    "PROTECTED_PATH_PARTS",
    "PROTECTED_SUFFIXES",
    "ApprovalRequired",
    "Decision",
    "decide_delete",
    "has_do_not_delete_marker",
    "is_in_progress",
    "is_protected_game_file",
    "require_execute",
]
