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

import time

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
        self, board_id: str, title: str, image_url: str | None = None
    ) -> Item:
        """Add a new food item to a board and broadcast ``ItemAdded``.

        Implements the design's ``add_item`` specification and Requirements
        1.1, 1.2, 1.3, 7.1, 7.3.

        Behavior:
        - Validates the title: ``title.strip()`` must be non-empty, otherwise
          a :class:`ValidationError` is raised with no state change and no
          broadcast (Req 7.1).
        - Lazily creates the board if it does not already exist.
        - Creates exactly one new :class:`Item` with a unique id, the trimmed
          title (Req 7.3), the optional ``image_url`` (Req 1.3), ``claimed_by``
          unset (so derived status is Available), and the current timestamp.
          No existing item is modified (Req 1.1).
        - Persists the item so it is retrievable in the snapshot (Req 1.2).
        - Awaits exactly one ``ItemAdded`` broadcast, then returns the item.

        Args:
            board_id: Target board id.
            title: Proposed item title (validated and trimmed).
            image_url: Optional image reference associated with the item.

        Returns:
            The newly created, Available :class:`Item`.

        Raises:
            ValidationError: If the trimmed title is empty.
        """
        # Reject invalid input before any state change or broadcast (Req 7.1).
        trimmed_title = self._require_non_empty(title, "Item title")

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
