"""Potluck business logic.

The ``PotluckService`` holds all mutation and query logic for a board. It
validates and applies mutations against the :class:`BoardStore`, derives state
(status, people grouping), and emits exactly one :class:`ChangeEvent` per
successful mutation through the :class:`WebSocketHub`.

Async model: ``WebSocketHub.broadcast`` is a coroutine, so every mutation that
broadcasts an event is itself an ``async`` method and ``await``\\s the broadcast
directly. The REST handlers (FastAPI) and the WebSocket endpoint are async and
``await`` these methods, so the whole service is async end to end. A successful
mutation returns its :class:`Item` and awaits exactly one broadcast; a failed
mutation (validation/conflict/permission) raises before any state change and
broadcasts nothing.
"""

from __future__ import annotations

import datetime
import hmac
import re
import time
from typing import Any

# Re-export domain exceptions for convenience to service-layer consumers.
from .exceptions import (  # noqa: F401
    ConflictError,
    NotFoundError,
    PermissionError,
    ValidationError,
)
from .hub import WebSocketHub
from .models import (
    BoardState,
    ChangeEvent,
    ChangeType,
    Item,
    PersonContribution,
)
from .store import BoardStore, generate_item_id


TITLE_MAX = 100
ADDED_BY_MAX = 60
EMPLOYEE_ID_MAX = 30

# Strict ASCII shape checked before ``fromisoformat``, which on Python 3.11+
# also accepts shapes such as ``20261012`` or ``2026-W41-1`` (Req 9.5).
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# One constant message for every rejected-ownership delete, independent of the
# stored and submitted ids (Req 11.5).
DELETE_FORBIDDEN_MESSAGE = "You are not allowed to delete this item."


def _require_text(value: Any, label: str, max_len: int) -> str:
    """Validate and trim a required text field.

    Raises :class:`ValidationError` for non-string, blank or over-long values.
    Messages name the field only; they never include the submitted value.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{label} is required.")
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{label} must not be empty.")
    if len(trimmed) > max_len:
        raise ValidationError(f"{label} must be at most {max_len} characters long.")
    return trimmed


def _require_date(value: Any, label: str = "Date") -> str:
    """Validate a ``YYYY-MM-DD`` calendar date and return the trimmed string."""
    if not isinstance(value, str):
        raise ValidationError(f"{label} is required.")
    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{label} must not be empty.")
    if _DATE_RE.fullmatch(trimmed) is None:
        raise ValidationError(f"{label} must be a valid date in YYYY-MM-DD format.")
    try:
        datetime.date.fromisoformat(trimmed)
    except ValueError:
        raise ValidationError(
            f"{label} must be a valid date in YYYY-MM-DD format."
        ) from None
    return trimmed


def _norm_id(value: Any) -> str | None:
    """Normalize an employee id for comparison (trim + casefold).

    ``None``, blank and non-string values count as missing (``None``).
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _ids_match(stored: Any, submitted: Any) -> bool:
    """Constant-time comparison of two normalized employee ids."""
    a = _norm_id(stored)
    b = _norm_id(submitted)
    if a is None or b is None:
        return False
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class PotluckService:
    """Core business logic for a shared potluck board.

    Composes a :class:`BoardStore` (persistence) and a :class:`WebSocketHub`
    (realtime fan-out). Mutations validate input, apply changes to the store,
    and broadcast a single change event; queries derive read-only views.
    """

    def __init__(self, store: BoardStore, hub: WebSocketHub) -> None:
        self._store = store
        self._hub = hub

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _require_non_empty(value: str | None, field_name: str) -> str:
        """Validate and trim a required string field.

        Returns the trimmed value when it is non-empty. Raises
        :class:`ValidationError` otherwise so the caller can reject the request
        before making any state change or broadcasting (Req 7.1). The trimmed
        value is what callers persist (Req 7.3).
        """
        trimmed = value.strip() if value is not None else ""
        if not trimmed:
            raise ValidationError(f"{field_name} must not be empty.")
        return trimmed

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    async def add_item(
        self,
        board_id: str,
        title: Any,
        added_by: Any,
        employee_id: Any,
        bring_date: Any,
        image_url: str | None = None,
    ) -> Item:
        """Add a new food item to a board and broadcast ``ItemAdded``.

        Implements Requirements 1.1, 1.2, 1.3, 7.1, 7.3 and 9.1 to 9.7, 9.11.

        All four values (title <= 100, added_by <= 60, employee_id <= 30 and a
        real ``YYYY-MM-DD`` date) are validated and trimmed BEFORE the store is
        touched, so a rejected request creates no board, changes no state and
        broadcasts nothing. The trimmed values are stored on the new Available
        item and exactly one ``ItemAdded`` event is broadcast.

        Raises:
            ValidationError: If any of the four values is invalid.
        """
        trimmed_title = _require_text(title, "Item title", TITLE_MAX)
        trimmed_added_by = _require_text(added_by, "Your name", ADDED_BY_MAX)
        trimmed_employee_id = _require_text(
            employee_id, "Employee ID", EMPLOYEE_ID_MAX
        )
        trimmed_date = _require_date(bring_date, "Date")
        if image_url is not None and not isinstance(image_url, str):
            raise ValidationError("Image link must be text.")

        # Lazily create the board if missing.
        if self._store.get_board(board_id) is None:
            self._store.create_board(board_id)

        item = Item(
            id=generate_item_id(),
            board_id=board_id,
            title=trimmed_title,
            image_url=image_url,
            claimed_by=None,
            created_at=time.time(),
            added_by=trimmed_added_by,
            employee_id=trimmed_employee_id,
            bring_date=trimmed_date,
        )
        self._store.save_item(board_id, item)

        # Exactly one event on success (Req 1.1 fan-out via the hub).
        await self._hub.broadcast(
            board_id, ChangeEvent(ChangeType.ITEM_ADDED, board_id, item)
        )
        return item

    async def claim_item(self, board_id: str, item_id: str, name: str) -> Item:
        """Claim an available item (atomic per-item read-modify-write).

        Implements the design's ``claim_item`` specification and the Claim Item
        Algorithm, covering Requirements 2.1, 2.2, 7.2, 7.3, 7.4.

        Behavior:
        - Validates the participant name before touching any state: ``name``
          must be non-empty after trimming, otherwise a :class:`ValidationError`
          is raised with no state change and no broadcast (Req 7.2).
        - Serializes the read-modify-write of this single item via the store's
          per-item lock so two concurrent claims can never both succeed
          (Req 2.2). Locks for different items are independent, so unrelated
          claims never contend.
        - Inside the critical section: loads the item and raises
          :class:`NotFoundError` when it does not exist (Req 7.4); raises
          :class:`ConflictError` carrying the current claimer when the item is
          already taken, leaving state unchanged and broadcasting nothing
          (Req 2.2).
        - On success: records the trimmed name as ``claimed_by`` (Req 7.3) so
          the derived status becomes ``Already Taken`` (Req 2.1), persists the
          item, then awaits exactly one ``ItemClaimed`` broadcast after the
          critical section and returns the item.

        Args:
            board_id: Board that owns the item.
            item_id: Item being claimed.
            name: Participant display name (validated and trimmed).

        Returns:
            The now ``Already Taken`` :class:`Item`.

        Raises:
            ValidationError: If the trimmed name is empty.
            NotFoundError: If no item exists for ``board_id``/``item_id``.
            ConflictError: If the item is already claimed; ``current_claimer``
                reports who currently holds it.
        """
        # Reject invalid input before acquiring the lock or mutating (Req 7.2).
        trimmed_name = self._require_non_empty(name, "Participant name")

        # Serialize the read-modify-write for this specific item so concurrent
        # claims cannot both observe it as Available (Req 2.2).
        with self._store.item_lock(board_id, item_id):
            item = self._store.get_item(board_id, item_id)
            if item is None:
                raise NotFoundError(
                    f"Item {item_id!r} not found on board {board_id!r}."
                )
            if item.claimed_by is not None:
                # Already taken: no state change, no broadcast (Req 2.2).
                raise ConflictError(current_claimer=item.claimed_by)

            item.claimed_by = trimmed_name
            self._store.save_item(board_id, item)

        # Exactly one event on success, after releasing the lock (Req 2.1).
        await self._hub.broadcast(
            board_id, ChangeEvent(ChangeType.ITEM_CLAIMED, board_id, item)
        )
        return item

    async def unclaim_item(self, board_id: str, item_id: str, name: str) -> Item:
        """Release an item previously claimed by ``name`` (ownership checked).

        Implements the design's ``unclaim_item`` specification and Requirements
        2.3, 2.4, 7.2.

        Behavior:
        - Validates the participant name before touching any state: ``name``
          must be non-empty after trimming, otherwise a :class:`ValidationError`
          is raised with no state change and no broadcast (Req 7.2).
        - Serializes the read-modify-write of this single item via the store's
          per-item lock so the ownership check and release are atomic with
          respect to concurrent claims/unclaims of the same item.
        - Inside the critical section: loads the item and raises
          :class:`NotFoundError` when it does not exist. Enforces ownership:
          only the current claimer may release the item. The current claimer is
          compared to ``name`` case-insensitively on their trimmed values; if
          the item is unclaimed (nobody to release) or claimed by someone else,
          a :class:`PermissionError` is raised with no state change and no
          broadcast (Req 2.4).
        - On success: clears ``claimed_by`` (so the derived status becomes
          ``Available``, Req 2.3), persists the item, then awaits exactly one
          ``ItemUnclaimed`` broadcast after the critical section and returns the
          item.

        Args:
            board_id: Board that owns the item.
            item_id: Item being released.
            name: Participant display name (validated and trimmed).

        Returns:
            The now ``Available`` :class:`Item`.

        Raises:
            ValidationError: If the trimmed name is empty.
            NotFoundError: If no item exists for ``board_id``/``item_id``.
            PermissionError: If the item is unclaimed or claimed by someone
                other than ``name`` (case-insensitive).
        """
        # Reject invalid input before acquiring the lock or mutating (Req 7.2).
        trimmed_name = self._require_non_empty(name, "Participant name")

        # Serialize the ownership check and release for this specific item so it
        # is atomic with respect to concurrent claims/unclaims.
        with self._store.item_lock(board_id, item_id):
            item = self._store.get_item(board_id, item_id)
            if item is None:
                raise NotFoundError(
                    f"Item {item_id!r} not found on board {board_id!r}."
                )

            # Only the current claimer may release the item. Treat an unclaimed
            # item (nobody to release) and a mismatched claimer the same way:
            # forbidden, with no state change and no broadcast (Req 2.4).
            current = item.claimed_by.strip() if item.claimed_by else ""
            if not current or current.lower() != trimmed_name.lower():
                raise PermissionError(
                    f"Item {item_id!r} can only be released by its current "
                    f"claimer."
                )

            item.claimed_by = None  # Derived status becomes Available (Req 2.3).
            self._store.save_item(board_id, item)

        # Exactly one event on success, after releasing the lock (Req 2.3).
        await self._hub.broadcast(
            board_id, ChangeEvent(ChangeType.ITEM_UNCLAIMED, board_id, item)
        )
        return item

    async def delete_item(
        self, board_id: str, item_id: str, employee_id: Any
    ) -> Item:
        """Delete an Available item after an ownership check.

        Checks run inside the per-item lock in this exact order:

        1. missing item -> :class:`NotFoundError`
        2. stored employee id set and submitted id missing/mismatched ->
           :class:`PermissionError` with one constant generic message
        3. item claimed -> :class:`ConflictError` (bringer must release first)
        4. ``store.delete_item`` (``NotFoundError`` if it removed nothing)

        Legacy items (stored ``employee_id`` is ``None``) skip step 2. Exactly
        one ``ItemDeleted`` is broadcast after the lock is released; nothing is
        broadcast on failure. The stored id is used for no other purpose.
        """
        with self._store.item_lock(board_id, item_id):
            item = self._store.get_item(board_id, item_id)
            if item is None:
                raise NotFoundError(
                    f"Item {item_id!r} not found on board {board_id!r}."
                )
            if item.employee_id is not None and not _ids_match(
                item.employee_id, employee_id
            ):
                raise PermissionError(DELETE_FORBIDDEN_MESSAGE)
            if item.claimed_by is not None:
                raise ConflictError(
                    current_claimer=item.claimed_by,
                    message=(
                        "This item has already been claimed; the bringer must "
                        "release it first."
                    ),
                )
            if not self._store.delete_item(board_id, item_id):
                raise NotFoundError(
                    f"Item {item_id!r} not found on board {board_id!r}."
                )

        await self._hub.broadcast(
            board_id, ChangeEvent(ChangeType.ITEM_DELETED, board_id, item)
        )
        return item

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def list_people(self, board_id: str) -> list[PersonContribution]:
        """Group claimed items by claimer name (Req 5.1, 5.2).

        Claimer names are keyed by their trimmed, lowercased form so "Ravi" and
        "ravi" are the same person, while the first-seen original casing is kept
        as the contribution's display name. Items appear under their claimer in
        creation order, contributions are returned in first-seen order, and
        unclaimed items never appear. An unknown or empty board yields ``[]``.
        """
        groups: dict[str, PersonContribution] = {}
        for item in self._store.list_items(board_id):
            claimer = item.claimed_by.strip() if item.claimed_by else ""
            if not claimer:
                continue
            key = claimer.lower()
            person = groups.get(key)
            if person is None:
                person = PersonContribution(name=claimer, items=[])
                groups[key] = person
            person.items.append(item)
        return list(groups.values())

    def get_board_state(self, board_id: str) -> BoardState:
        """Return the full board snapshot: items plus grouped people (Req 3.2).

        Never raises for an unknown board; it returns empty lists because boards
        are created lazily on first add.
        """
        return BoardState(
            board_id=board_id,
            items=self._store.list_items(board_id),
            people=self.list_people(board_id),
        )
