"""Board persistence layer.

Defines the storage interface (``BoardStore``) that later layers type against,
plus the concrete in-memory implementation (``InMemoryBoardStore``) used for the
MVP. The in-memory store keeps boards and items in dictionaries, generates
unguessable board ids and unique item ids, and exposes a per-item lock so the
service can make the claim read-modify-write atomic.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from typing import Protocol

from .models import Board, Item


class BoardStore(Protocol):
    """Persistence and retrieval of board state."""

    def get_board(self, board_id: str) -> Board | None: ...

    def create_board(self, board_id: str) -> Board: ...

    def save_item(self, board_id: str, item: Item) -> None: ...

    def get_item(self, board_id: str, item_id: str) -> Item | None: ...

    def list_items(self, board_id: str) -> list[Item]: ...

    def delete_item(self, board_id: str, item_id: str) -> bool: ...

    def delete_expired(self, before_date: str) -> list[Item]: ...


def generate_board_id() -> str:
    """Return an unguessable, URL-safe board id.

    Uses ``secrets.token_urlsafe`` so board links cannot be trivially enumerated
    (see the design's Security Considerations).
    """
    return secrets.token_urlsafe(12)


def generate_item_id() -> str:
    """Return a unique item id (a hex uuid4)."""
    return uuid.uuid4().hex


class InMemoryBoardStore:
    """In-memory implementation of :class:`BoardStore`.

    State is held in two nested dictionaries:

    - ``_boards``: ``board_id -> Board``
    - ``_items``: ``board_id -> {item_id -> Item}`` (insertion order preserved)

    Access is guarded by a coarse structural lock (``_lock``) that protects the
    dictionary structure itself. In addition, the store hands out a dedicated
    per-item lock via :meth:`item_lock` so the service can serialize the
    read-modify-write of a single item (the claim critical section) without
    blocking operations on unrelated items.
    """

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}
        # Insertion order of the inner dict preserves item creation order, which
        # drives the ordering returned by ``list_items``.
        self._items: dict[str, dict[str, Item]] = {}
        self._lock = threading.RLock()
        # Per-item locks keyed by (board_id, item_id). Created lazily.
        self._item_locks: dict[tuple[str, str], threading.RLock] = {}
        self._item_locks_guard = threading.Lock()

    # ------------------------------------------------------------------
    # Board operations
    # ------------------------------------------------------------------
    def get_board(self, board_id: str) -> Board | None:
        """Return the board with ``board_id`` or ``None`` if it does not exist."""
        with self._lock:
            return self._boards.get(board_id)

    def create_board(self, board_id: str) -> Board:
        """Create and store a board under ``board_id``.

        If a board already exists for the id, the existing board is returned
        unchanged so creation is idempotent and never discards items.
        """
        with self._lock:
            existing = self._boards.get(board_id)
            if existing is not None:
                return existing
            board = Board(id=board_id, created_at=time.time())
            self._boards[board_id] = board
            self._items.setdefault(board_id, {})
            return board

    # ------------------------------------------------------------------
    # Item operations
    # ------------------------------------------------------------------
    def save_item(self, board_id: str, item: Item) -> None:
        """Persist ``item`` under ``board_id``.

        The board bucket is created lazily if needed so the service can save an
        item immediately after lazily creating a board. Saving an item that
        already exists updates it in place while preserving its original
        insertion order.
        """
        with self._lock:
            bucket = self._items.setdefault(board_id, {})
            bucket[item.id] = item

    def get_item(self, board_id: str, item_id: str) -> Item | None:
        """Return the item or ``None`` if the board or item does not exist."""
        with self._lock:
            bucket = self._items.get(board_id)
            if bucket is None:
                return None
            return bucket.get(item_id)

    def list_items(self, board_id: str) -> list[Item]:
        """Return all items for the board in insertion (creation) order.

        Returns an empty list when the board has no items or does not exist.
        """
        with self._lock:
            bucket = self._items.get(board_id)
            if not bucket:
                return []
            return list(bucket.values())

    def delete_item(self, board_id: str, item_id: str) -> bool:
        """Remove one item; return True if it existed.

        Other items keep their relative order and other boards are untouched.
        The per-item lock entry is deliberately kept: a waiting claimer may
        still hold that lock object, and replacing it would break exclusivity.
        """
        with self._lock:
            bucket = self._items.get(board_id)
            if bucket is None:
                return False
            return bucket.pop(item_id, None) is not None

    def delete_expired(self, before_date: str) -> list[Item]:
        """Remove every item with a ``bring_date`` strictly before ``before_date``.

        Works across all boards (ISO dates compare correctly as strings), keeps
        the relative order of the remaining items, never touches items whose
        ``bring_date`` is ``None`` and returns the removed items.
        """
        removed: list[Item] = []
        with self._lock:
            for bucket in self._items.values():
                expired_ids = [
                    item_id
                    for item_id, item in bucket.items()
                    if item.bring_date is not None and item.bring_date < before_date
                ]
                for item_id in expired_ids:
                    removed.append(bucket.pop(item_id))
        return removed

    # ------------------------------------------------------------------
    # Concurrency helper
    # ------------------------------------------------------------------
    def item_lock(self, board_id: str, item_id: str) -> threading.RLock:
        """Return the dedicated lock for a single item.

        The service uses this to make the claim read-modify-write atomic:

            with store.item_lock(board_id, item_id):
                item = store.get_item(board_id, item_id)
                ...  # inspect and mutate
                store.save_item(board_id, item)

        The same lock object is returned for the same ``(board_id, item_id)``
        across calls, and locks for different items are independent so claims on
        unrelated items never contend.
        """
        key = (board_id, item_id)
        with self._item_locks_guard:
            lock = self._item_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._item_locks[key] = lock
            return lock
