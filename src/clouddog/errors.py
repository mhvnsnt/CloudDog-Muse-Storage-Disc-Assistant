"""Exception hierarchy for CloudDog.

A DEGRADED backend is not an error to crash on: backends report Health and
raise StoreDegraded only when an operation is attempted while degraded, so
callers can route around them. Verification failures are always fatal to the
delete step — never delete on a failed verify.
"""
from __future__ import annotations


class CloudDogError(Exception):
    """Base for all CloudDog errors."""


class StoreDegraded(CloudDogError):
    """Raised when an operation is attempted on a DEGRADED/DOWN backend."""

    def __init__(self, backend: str, reason: str):
        self.backend = backend
        self.reason = reason
        super().__init__(f"store backend '{backend}' unavailable: {reason}")


class VerificationFailed(CloudDogError):
    """Byte-verification of a stored object failed. Never delete on this."""


class PolicyViolation(CloudDogError):
    """An action was attempted that policy forbids (e.g. deleting sacred)."""
