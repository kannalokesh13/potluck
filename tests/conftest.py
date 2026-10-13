"""Shared fakes and factories for the potluck test suite."""

from __future__ import annotations

import datetime
import os

# Keep the local .env (real database URL, pinned board) out of the test suite so
# tests never touch real data. Must be set before app.main is imported.
os.environ.setdefault("POTLUCK_NO_DOTENV", "1")

from app.hub import WebSocketHub
from app.models import ChangeEvent
from app.service import PotluckService
from app.store import InMemoryBoardStore


class FakeConnection:
    """WebSocket-like connection that records every payload it is sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)


class FailingConnection:
    """Connection whose send always raises (simulates a dropped socket)."""

    def __init__(self) -> None:
        self.attempts = 0

    async def send_text(self, data: str) -> None:
        self.attempts += 1
        raise RuntimeError("socket closed")


class FakeHub(WebSocketHub):
    """Hub that records every broadcast event in addition to real fan-out."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, ChangeEvent]] = []

    async def broadcast(self, board_id: str, event: ChangeEvent) -> None:
        self.events.append((board_id, event))
        await super().broadcast(board_id, event)


def add(service, board, title="Dish", by="Asha", emp="E1", date="2026-10-12", image=None):
    """Add an item through the service from synchronous test code."""
    import asyncio

    return asyncio.run(service.add_item(board, title, by, emp, date, image))


VALID_BODY = {
    "title": "Dish",
    "added_by": "Asha",
    "employee_id": "E1",
    "bring_date": "2026-10-12",
}


FIXED_TODAY = datetime.date(2000, 1, 1)


def make_service(hub: WebSocketHub | None = None, today=None):
    """Return a fresh (service, store, hub) triple with no shared state.

    The clock is fixed at 2000-01-01 by default so tests with any later date
    stay deterministic; pass ``today`` (a date) to override it.
    """
    store = InMemoryBoardStore()
    hub = hub if hub is not None else FakeHub()
    day = today if today is not None else FIXED_TODAY
    return PotluckService(store, hub, today=lambda: day), store, hub
