"""Custom exceptions used by the PotluckService layer.

These map to HTTP responses at the REST API boundary:
- ValidationError  -> 400 Bad Request
- NotFoundError    -> 404 Not Found
- ConflictError    -> 409 Conflict
- PermissionError  -> 403 Forbidden
"""

from __future__ import annotations


class PotluckError(Exception):
    """Base class for all domain-level errors raised by the service layer."""


class ValidationError(PotluckError):
    """Raised when input fails validation (e.g., empty-trimmed title or name).

    Maps to HTTP 400 Bad Request.
    """


class NotFoundError(PotluckError):
    """Raised when a referenced board or item does not exist.

    Maps to HTTP 404 Not Found.
    """


class ConflictError(PotluckError):
    """Raised when claiming an item that is already taken.

    Carries the current claimer's name so the API can report who holds the item.
    Maps to HTTP 409 Conflict.
    """

    def __init__(self, current_claimer: str, message: str | None = None) -> None:
        self.current_claimer = current_claimer
        super().__init__(message or f"Item already claimed by {current_claimer!r}")


class PermissionError(PotluckError):  # noqa: A001 - intentional domain name
    """Raised when a participant tries to release an item claimed by someone else.

    Shadows the builtin ``PermissionError`` within this package by design, matching
    the design document's naming. Maps to HTTP 403 Forbidden.
    """
