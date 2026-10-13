"""Unit tests for WebSocketHub (task 3.2)."""

from __future__ import annotations

import asyncio
import json

from app.hub import WebSocketHub, event_to_dict, event_to_json, item_to_dict
from app.models import (
    BoardState,
    ChangeEvent,
    ChangeType,
    Item,
    PersonContribution,
)
from tests.conftest import FailingConnection, FakeConnection


def _event(board_id: str = "b1", claimed_by: str | None = None) -> ChangeEvent:
    item = Item("i1", board_id, "Biryani", None, claimed_by, 1.0)
    kind = ChangeType.ITEM_CLAIMED if claimed_by else ChangeType.ITEM_ADDED
    return ChangeEvent(kind, board_id, item)


def test_broadcast_reaches_all_subscribers_and_none_on_other_boards():
    hub = WebSocketHub()
    a, b, other = FakeConnection(), FakeConnection(), FakeConnection()
    hub.subscribe("b1", a)
    hub.subscribe("b1", b)
    hub.subscribe("b2", other)

    asyncio.run(hub.broadcast("b1", _event("b1")))

    assert len(a.sent) == 1 and len(b.sent) == 1
    assert other.sent == []
    assert a.sent == b.sent


PUBLIC_KEYS = {
    "id", "board_id", "title", "image_url", "claimed_by",
    "created_at", "status", "added_by", "employee_id", "bring_date",
}  # fmt: skip


def _new_item(employee_id: str = "EMP-SECRET-42") -> Item:
    return Item(
        "i1", "b1", "Biryani", None, None, 1.0,
        added_by="Asha", employee_id=employee_id, bring_date="2026-10-12",
    )  # fmt: skip


def test_item_to_dict_has_exact_public_keys_including_employee_id():
    d = item_to_dict(_new_item())
    assert set(d) == PUBLIC_KEYS
    assert d["employee_id"] == "EMP-SECRET-42"
    assert d["added_by"] == "Asha"
    assert d["bring_date"] == "2026-10-12"


def test_item_to_dict_legacy_item_has_none_for_new_public_fields():
    d = item_to_dict(Item("i1", "b1", "Biryani", None, None, 1.0))
    assert set(d) == PUBLIC_KEYS
    assert d["added_by"] is None
    assert d["employee_id"] is None
    assert d["bring_date"] is None


def test_reprs_do_not_contain_employee_id():
    item = _new_item()
    claimed = _new_item()
    claimed.claimed_by = "Ravi"
    texts = [
        repr(item),
        repr(BoardState("b1", [item], [PersonContribution("Ravi", [claimed])])),
        repr(PersonContribution("Ravi", [claimed])),
        repr(ChangeEvent(ChangeType.ITEM_ADDED, "b1", item)),
    ]
    for text in texts:
        assert "EMP-SECRET-42" not in text


def test_item_deleted_event_shape_includes_employee_id():
    event = ChangeEvent(ChangeType.ITEM_DELETED, "b1", _new_item())
    d = event_to_dict(event)
    assert set(d) == {"type", "board_id", "item"}
    assert d["type"] == "ItemDeleted"
    assert d["board_id"] == "b1"
    assert set(d["item"]) == PUBLIC_KEYS
    assert d["item"]["employee_id"] == "EMP-SECRET-42"
    assert json.loads(event_to_json(event))["item"]["employee_id"] == "EMP-SECRET-42"


def test_broadcast_with_no_subscribers_is_noop():
    asyncio.run(WebSocketHub().broadcast("nobody", _event("nobody")))


def test_payload_shape():
    hub = WebSocketHub()
    conn = FakeConnection()
    hub.subscribe("b1", conn)

    asyncio.run(hub.broadcast("b1", _event("b1", claimed_by="Ravi")))

    payload = json.loads(conn.sent[0])
    assert payload["type"] == "ItemClaimed"
    assert payload["board_id"] == "b1"
    item = payload["item"]
    assert item["id"] == "i1"
    assert item["title"] == "Biryani"
    assert item["claimed_by"] == "Ravi"
    assert item["status"] == "Already Taken"


def test_payload_status_available_for_unclaimed():
    hub = WebSocketHub()
    conn = FakeConnection()
    hub.subscribe("b1", conn)
    asyncio.run(hub.broadcast("b1", _event("b1")))
    payload = json.loads(conn.sent[0])
    assert payload["type"] == "ItemAdded"
    assert payload["item"]["status"] == "Available"
    assert payload["item"]["claimed_by"] is None


def test_failing_connection_dropped_while_others_still_receive():
    hub = WebSocketHub()
    bad, good = FailingConnection(), FakeConnection()
    hub.subscribe("b1", bad)
    hub.subscribe("b1", good)
    assert hub.subscriber_count("b1") == 2

    asyncio.run(hub.broadcast("b1", _event("b1")))

    assert len(good.sent) == 1
    assert bad.attempts == 1
    assert hub.subscriber_count("b1") == 1

    # The failed connection is gone: a second broadcast doesn't retry it.
    asyncio.run(hub.broadcast("b1", _event("b1")))
    assert bad.attempts == 1
    assert len(good.sent) == 2


def test_unsubscribe_stops_delivery_and_is_idempotent():
    hub = WebSocketHub()
    conn = FakeConnection()
    hub.subscribe("b1", conn)
    hub.unsubscribe(conn)
    hub.unsubscribe(conn)  # no-op
    hub.unsubscribe(FakeConnection())  # unknown, no-op
    assert hub.subscriber_count("b1") == 0

    asyncio.run(hub.broadcast("b1", _event("b1")))
    assert conn.sent == []


def test_resubscribe_moves_connection_to_new_board():
    hub = WebSocketHub()
    conn = FakeConnection()
    hub.subscribe("b1", conn)
    hub.subscribe("b2", conn)
    assert hub.subscriber_count("b1") == 0
    assert hub.subscriber_count("b2") == 1

    asyncio.run(hub.broadcast("b1", _event("b1")))
    assert conn.sent == []
    asyncio.run(hub.broadcast("b2", _event("b2")))
    assert len(conn.sent) == 1


def test_subscribe_same_board_twice_does_not_duplicate():
    hub = WebSocketHub()
    conn = FakeConnection()
    hub.subscribe("b1", conn)
    hub.subscribe("b1", conn)
    assert hub.subscriber_count("b1") == 1
    asyncio.run(hub.broadcast("b1", _event("b1")))
    assert len(conn.sent) == 1
