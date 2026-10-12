"""WebSocket hub / broadcaster.

The ``WebSocketHub`` manages per-board client subscriptions and broadcasts
``ChangeEvent`` payloads to every client subscribed to the affected board. It
tracks active connections grouped by board id, serializes events to JSON, and
cleans up connections that drop or error during a send.

Connections are duck-typed: any object exposing an awaitable ``send_text``
(or, as a fallback, ``send``) coroutine method works. This matches
Starlette/FastAPI ``WebSocket`` objects while keeping the hub importable and
unit-testable without a live server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol, runtime_checkable

from .models import ChangeEvent, Item


@runtime_checkable
class Connection(Protocol):
    """Minimal async WebSocket-like interface the hub broadcasts over."""

    async def send_text(self, data: str) -> None: ...


def item_to_dict(item: Item) -> dict[str, Any]:
    """Serialize an ``Item`` to a plain JSON-ready dict.

    The derived ``status`` is included (its string value) so clients never have
    to recompute it. ``claimed_by`` and ``image_url`` are passed through as-is
    (either a string or ``None``).
    """
    return {
        "id": item.id,
        "board_id": item.board_id,
        "title": item.title,
        "image_url": item.image_url,
        "claimed_by": item.claimed_by,
        "created_at": item.created_at,
        "status": item.status.value,
    }


def event_to_dict(event: ChangeEvent) -> dict[str, Any]:
    """Serialize a ``ChangeEvent`` (and its ``Item``) to a JSON-ready dict."""
    return {
        "type": event.type.value,
        "board_id": event.board_id,
        "item": item_to_dict(event.item),
    }


def event_to_json(event: ChangeEvent) -> str:
    """Serialize a ``ChangeEvent`` to a compact JSON string."""
    return json.dumps(event_to_dict(event))


class WebSocketHub:
    """Manage per-board client subscriptions and broadcast change events.

    Responsibilities (per design "Component 3: WebSocketHub"):
    - Track active connections grouped by board id.
    - Serialize ``ChangeEvent`` objects and push to all subscribers of the
      affected board.
    - Clean up dropped connections (on explicit unsubscribe or on send failure).
    """

    def __init__(self) -> None:
        # board_id -> set of connections subscribed to that board.
        self._boards: dict[str, set[Connection]] = {}
        # reverse index: connection -> board_id, so unsubscribe is O(1) and
        # does not need to scan every board.
        self._connection_board: dict[Connection, str] = {}

    def subscribe(self, board_id: str, connection: Connection) -> None:
        """Register ``connection`` as a subscriber of ``board_id``.

        If the connection was previously subscribed to a different board, it is
        first removed from that board so a connection only ever belongs to one
        board at a time.
        """
        existing = self._connection_board.get(connection)
        if existing is not None and existing != board_id:
            self._remove(connection, existing)

        self._boards.setdefault(board_id, set()).add(connection)
        self._connection_board[connection] = board_id

    def unsubscribe(self, connection: Connection) -> None:
        """Remove ``connection`` from whatever board it is subscribed to.

        Safe to call for an unknown/already-removed connection (no-op).
        """
        board_id = self._connection_board.get(connection)
        if board_id is None:
            return
        self._remove(connection, board_id)

    def _remove(self, connection: Connection, board_id: str) -> None:
        """Internal: drop ``connection`` from ``board_id`` and the reverse index."""
        subscribers = self._boards.get(board_id)
        if subscribers is not None:
            subscribers.discard(connection)
            if not subscribers:
                # Avoid leaking empty board buckets.
                del self._boards[board_id]
        self._connection_board.pop(connection, None)

    def subscriber_count(self, board_id: str) -> int:
        """Return the number of connections currently subscribed to ``board_id``."""
        return len(self._boards.get(board_id, ()))

    async def broadcast(self, board_id: str, event: ChangeEvent) -> None:
        """Serialize ``event`` and send it to every subscriber of ``board_id``.

        Sends are awaited per connection. Any connection whose send raises
        (closed socket, network error, etc.) is treated as dropped and removed
        from the hub. Subscribers added/removed during the broadcast are not
        affected because we snapshot the subscriber set up front.
        """
        subscribers = self._boards.get(board_id)
        if not subscribers:
            return

        payload = event_to_json(event)

        # Snapshot so cleanup during iteration does not mutate the live set.
        for connection in list(subscribers):
            try:
                await self._send(connection, payload)
            except Exception:
                # Connection errored or is closed: clean it up.
                self._remove(connection, board_id)

    @staticmethod
    async def _send(connection: Connection, payload: str) -> None:
        """Send ``payload`` over ``connection`` using the best available method.

        Prefers ``send_text`` (Starlette/FastAPI WebSocket); falls back to a
        generic ``send`` coroutine if present. Raises if neither is available or
        the underlying send fails.
        """
        send_text = getattr(connection, "send_text", None)
        if callable(send_text):
            await send_text(payload)
            return

        send = getattr(connection, "send", None)
        if callable(send):
            result = send(payload)
            if asyncio.iscoroutine(result):
                await result
            return

        raise TypeError(
            "Connection object has no awaitable 'send_text' or 'send' method"
        )
