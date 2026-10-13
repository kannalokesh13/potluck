"""Automatic expiry of past-dated items: clock, store, service, API, lifespan."""

from __future__ import annotations

import asyncio
import datetime
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import clock
from app.api import create_router, register_exception_handlers
from app.exceptions import ValidationError
from app.hub import WebSocketHub
from app.models import ChangeType, Item
from app.service import PotluckService
from app.store import InMemoryBoardStore
from tests.conftest import FakeHub, VALID_BODY, add, make_service

TODAY = datetime.date(2026, 10, 13)
YESTERDAY = "2026-10-12"
TODAY_S = "2026-10-13"
TOMORROW = "2026-10-14"


def _seed(store, board, item_id, bring_date, claimed_by=None):
    store.save_item(
        board,
        Item(
            id=item_id,
            board_id=board,
            title=item_id,
            image_url=None,
            claimed_by=claimed_by,
            created_at=1.0,
            bring_date=bring_date,
        ),
    )


def _ids(store, board):
    return [i.id for i in store.list_items(board)]


def _deleted(hub):
    return [(b, e) for b, e in hub.events if e.type is ChangeType.ITEM_DELETED]


# ------------------------------------------------------------- expiry rules
def test_boundary_yesterday_expired_today_and_tomorrow_kept():
    service, store, hub = make_service(today=TODAY)
    _seed(store, "b", "old", YESTERDAY)
    _seed(store, "b", "now", TODAY_S)
    _seed(store, "b", "next", TOMORROW)

    removed = asyncio.run(service.purge_expired())

    assert [i.id for i in removed] == ["old"]
    assert _ids(store, "b") == ["now", "next"]


def test_claimed_items_expire_and_legacy_none_never_expire():
    service, store, hub = make_service(today=TODAY)
    _seed(store, "b", "claimed-old", YESTERDAY, claimed_by="Ravi")
    _seed(store, "b", "legacy", None)

    removed = asyncio.run(service.purge_expired())

    assert [i.id for i in removed] == ["claimed-old"]
    assert _ids(store, "b") == ["legacy"]


def test_one_event_per_removed_item_and_none_when_nothing_expired():
    service, store, hub = make_service(today=TODAY)
    _seed(store, "b1", "a", YESTERDAY)
    _seed(store, "b1", "b", "2020-01-01")
    _seed(store, "b2", "c", YESTERDAY)
    _seed(store, "b2", "keep", TODAY_S)

    asyncio.run(service.purge_expired())

    events = _deleted(hub)
    assert sorted((b, e.item.id) for b, e in events) == [
        ("b1", "a"),
        ("b1", "b"),
        ("b2", "c"),
    ]
    assert all(b == e.board_id == e.item.board_id for b, e in events)
    assert len(hub.events) == 3  # nothing else was broadcast


def test_second_purge_is_a_noop():
    service, store, hub = make_service(today=TODAY)
    _seed(store, "b", "old", YESTERDAY)
    assert len(asyncio.run(service.purge_expired())) == 1
    before = len(hub.events)

    assert asyncio.run(service.purge_expired()) == []
    assert len(hub.events) == before


def test_people_view_and_snapshot_drop_expired():
    service, store, hub = make_service(today=TODAY)
    _seed(store, "b", "old", YESTERDAY, claimed_by="Ravi")
    _seed(store, "b", "new", TOMORROW, claimed_by="Meena")

    asyncio.run(service.purge_expired())

    state = service.get_board_state("b")
    assert [i.id for i in state.items] == ["new"]
    assert [p.name for p in state.people] == ["Meena"]
    assert [p.name for p in service.list_people("b")] == ["Meena"]


def test_one_failing_broadcast_does_not_stop_the_others():
    class FlakyHub(FakeHub):
        async def broadcast(self, board_id, event):
            await super().broadcast(board_id, event)
            if event.item.id == "a":
                raise RuntimeError("boom")

    service, store, hub = make_service(hub=FlakyHub(), today=TODAY)
    _seed(store, "b", "a", YESTERDAY)
    _seed(store, "b", "b", YESTERDAY)

    removed = asyncio.run(service.purge_expired())

    assert len(removed) == 2
    assert [e.item.id for _b, e in _deleted(hub)] == ["a", "b"]


# ------------------------------------------------------ past-date rejection
def test_add_item_rejects_past_date_without_side_effects():
    service, store, hub = make_service(today=TODAY)
    with pytest.raises(ValidationError, match="past"):
        add(service, "b", date=YESTERDAY)
    assert store.get_board("b") is None
    assert store.list_items("b") == []
    assert hub.events == []


def test_add_item_accepts_today_and_future():
    service, store, hub = make_service(today=TODAY)
    assert add(service, "b", date=TODAY_S).bring_date == TODAY_S
    assert add(service, "b", date=TOMORROW).bring_date == TOMORROW
    assert len(hub.events) == 2


def _api_client(today):
    store = InMemoryBoardStore()
    hub = FakeHub()
    service = PotluckService(store, hub, today=lambda: today)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_router(service, hub))
    return TestClient(app), store, hub


def test_api_past_date_is_400_and_today_is_201():
    client, store, hub = _api_client(TODAY)
    with client:
        bad = client.post("/board/x/items", json={**VALID_BODY, "bring_date": YESTERDAY})
        assert bad.status_code == 400
        assert "past" in bad.json()["detail"]
        assert store.list_items("x") == []
        assert hub.events == []

        ok = client.post("/board/x/items", json={**VALID_BODY, "bring_date": TODAY_S})
        assert ok.status_code == 201
        assert len(store.list_items("x")) == 1
        assert len(hub.events) == 1


# ------------------------------------------------------------------- clock
def test_utc_evening_is_already_next_day_in_kolkata():
    now = datetime.datetime(2026, 10, 13, 19, 0, tzinfo=datetime.timezone.utc)
    from zoneinfo import ZoneInfo

    assert clock.today_in_tz(now, ZoneInfo("Asia/Kolkata")) == datetime.date(2026, 10, 14)
    assert clock.today_in_tz(now, ZoneInfo("UTC")) == datetime.date(2026, 10, 13)


def test_default_timezone_is_kolkata(monkeypatch):
    monkeypatch.delenv("POTLUCK_TIMEZONE", raising=False)
    now = datetime.datetime(2026, 10, 13, 19, 0, tzinfo=datetime.timezone.utc)
    assert clock.today_in_tz(now) == datetime.date(2026, 10, 14)


def test_invalid_timezone_falls_back_without_raising(monkeypatch, capsys):
    monkeypatch.setenv("POTLUCK_TIMEZONE", "Not/AZone")
    tz = clock.get_timezone()
    assert str(tz) == clock.DEFAULT_TIMEZONE
    assert "warning" in capsys.readouterr().out
    assert isinstance(clock.today_in_tz(), datetime.date)


# ---------------------------------------------------------------- lifespan
def test_lifespan_purges_at_startup_and_shuts_down_cleanly(monkeypatch):
    import app.main as main

    store = InMemoryBoardStore()
    hub = WebSocketHub()
    service = PotluckService(store, hub, today=lambda: TODAY)
    _seed(store, "b", "old", YESTERDAY)
    _seed(store, "b", "keep", TODAY_S)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "service", service)

    started = time.monotonic()
    with TestClient(main.app) as c:
        assert [i.id for i in store.list_items("b")] == ["keep"]
        assert c.get("/healthz").status_code == 200
    assert time.monotonic() - started < 5


def test_lifespan_loop_purges_periodically_and_survives_errors(monkeypatch):
    import app.main as main

    store = InMemoryBoardStore()
    service = PotluckService(store, WebSocketHub(), today=lambda: TODAY)
    calls = {"n": 0}
    real = service.purge_expired

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("transient")
        return await real()

    monkeypatch.setattr(service, "purge_expired", flaky)
    monkeypatch.setattr(main, "store", store)
    monkeypatch.setattr(main, "service", service)
    monkeypatch.setattr(main, "purge_interval_seconds", lambda: 0.05)

    with TestClient(main.app):
        _seed(store, "b", "late-old", YESTERDAY)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and _ids(store, "b"):
            time.sleep(0.02)
        assert _ids(store, "b") == []
        assert calls["n"] >= 3  # startup, failing cycle, then a successful one


def test_purge_interval_parsing(monkeypatch):
    import app.main as main

    monkeypatch.delenv("POTLUCK_PURGE_MINUTES", raising=False)
    assert main.purge_interval_seconds() == 600
    monkeypatch.setenv("POTLUCK_PURGE_MINUTES", "3")
    assert main.purge_interval_seconds() == 180
    monkeypatch.setenv("POTLUCK_PURGE_MINUTES", "0")
    assert main.purge_interval_seconds() == 60
    monkeypatch.setenv("POTLUCK_PURGE_MINUTES", "abc")
    assert main.purge_interval_seconds() == 600


# ------------------------------------------------------- in-memory store
def test_store_delete_expired_keeps_order_and_spans_boards():
    store = InMemoryBoardStore()
    _seed(store, "b1", "a", "2020-01-01")
    _seed(store, "b1", "b", "2030-01-01")
    _seed(store, "b1", "c", None)
    _seed(store, "b1", "d", "2031-01-01")
    _seed(store, "b2", "e", "2020-05-05")

    removed = store.delete_expired("2025-01-01")

    assert sorted(i.id for i in removed) == ["a", "e"]
    assert {i.board_id for i in removed} == {"b1", "b2"}
    assert _ids(store, "b1") == ["b", "c", "d"]
    assert _ids(store, "b2") == []
