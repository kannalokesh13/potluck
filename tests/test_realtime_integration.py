"""End-to-end realtime tests over REST + WebSocket (task 12.1).

Covers Property 6 (fan-out to all subscribers of a board) and Property 7
(snapshot + events converge to a fresh snapshot).

**Validates: Requirements 3.1, 3.3**
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.test_api import build_app

B = "room-1"
OTHER = "room-2"


@pytest.fixture()
def env():
    app, hub = build_app()
    with TestClient(app) as client:
        yield client, hub


def apply_event(items: list[dict], event: dict) -> list[dict]:
    """Python mirror of the frontend applyEvent: add if absent, else replace by id."""
    item = event["item"]
    for idx, existing in enumerate(items):
        if existing["id"] == item["id"]:
            items[idx] = item
            return items
    items.append(item)
    return items


def wait_for(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_events_reach_all_sockets_and_converge_with_snapshot(env):
    client, hub = env
    with client.websocket_connect(f"/board/{B}/ws") as ws1, client.websocket_connect(
        f"/board/{B}/ws"
    ) as ws2:
        for ws in (ws1, ws2):
            ack = json.loads(ws.receive_text())
            assert ack == {"type": "subscribed", "board_id": B}
        assert hub.subscriber_count(B) == 2

        # Snapshot taken BEFORE any changes.
        snapshot = client.get(f"/board/{B}/state").json()["items"]
        assert snapshot == []

        item = client.post(f"/board/{B}/items", json={"title": "Biryani"}).json()
        claimed = client.post(
            f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"}
        ).json()

        received = {}
        for name, ws in (("ws1", ws1), ("ws2", ws2)):
            added = json.loads(ws.receive_text())
            claim = json.loads(ws.receive_text())
            assert added["type"] == "ItemAdded"
            assert added["board_id"] == B
            assert added["item"]["id"] == item["id"]
            assert added["item"]["status"] == "Available"
            assert claim["type"] == "ItemClaimed"
            assert claim["item"]["id"] == item["id"]
            assert claim["item"]["claimed_by"] == "Ravi"
            assert claim["item"]["status"] == "Already Taken"
            assert claim["item"] == claimed
            received[name] = [added, claim]

        # Property 7: stale snapshot + events == fresh snapshot.
        fresh = client.get(f"/board/{B}/state").json()["items"]
        for events in received.values():
            state = [dict(i) for i in snapshot]
            for ev in events:
                state = apply_event(state, ev)
            assert state == fresh

        # Unclaim also converges and is delivered to both.
        client.post(f"/board/{B}/items/{item['id']}/unclaim", json={"name": "ravi"})
        for ws in (ws1, ws2):
            ev = json.loads(ws.receive_text())
            assert ev["type"] == "ItemUnclaimed"
            assert ev["item"]["status"] == "Available"


def test_failed_mutations_send_no_event(env):
    client, _hub = env
    with client.websocket_connect(f"/board/{B}/ws") as ws:
        json.loads(ws.receive_text())  # ack
        item = client.post(f"/board/{B}/items", json={"title": "Dal"}).json()
        client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"})
        # Failures: conflict, forbidden, blank title.
        assert client.post(
            f"/board/{B}/items/{item['id']}/claim", json={"name": "Asha"}
        ).status_code == 409
        assert client.post(
            f"/board/{B}/items/{item['id']}/unclaim", json={"name": "Asha"}
        ).status_code == 403
        assert client.post(f"/board/{B}/items", json={"title": " "}).status_code == 400
        # A final successful mutation: the next event must be this one, proving
        # the failures produced no events in between.
        client.post(f"/board/{B}/items/{item['id']}/unclaim", json={"name": "Ravi"})

        types = [json.loads(ws.receive_text())["type"] for _ in range(3)]
        assert types == ["ItemAdded", "ItemClaimed", "ItemUnclaimed"]


def test_other_board_sockets_only_get_their_own_events(env):
    client, hub = env
    with client.websocket_connect(f"/board/{B}/ws") as ws_b, client.websocket_connect(
        f"/board/{OTHER}/ws"
    ) as ws_o:
        json.loads(ws_b.receive_text())
        json.loads(ws_o.receive_text())
        assert hub.subscriber_count(B) == 1
        assert hub.subscriber_count(OTHER) == 1

        # Mutate the OTHER board first, then B, then OTHER again.
        client.post(f"/board/{OTHER}/items", json={"title": "Other-1"})
        mine = client.post(f"/board/{B}/items", json={"title": "Mine"}).json()
        client.post(f"/board/{OTHER}/items", json={"title": "Other-2"})

        # Board B's socket got exactly one event: its own.
        ev = json.loads(ws_b.receive_text())
        assert ev["board_id"] == B
        assert ev["item"]["id"] == mine["id"]

        # Board OTHER's socket got its two, in order, and nothing from B.
        evs = [json.loads(ws_o.receive_text()) for _ in range(2)]
        assert [e["item"]["title"] for e in evs] == ["Other-1", "Other-2"]
        assert all(e["board_id"] == OTHER for e in evs)

        # Prove nothing else is queued for ws_b: ping/pong is the next message.
        ws_b.send_text("ping")
        assert ws_b.receive_text() == "pong"
        ws_o.send_text("ping")
        assert ws_o.receive_text() == "pong"


def test_disconnect_removes_subscriber(env):
    client, hub = env
    with client.websocket_connect(f"/board/{B}/ws") as ws:
        json.loads(ws.receive_text())
        assert hub.subscriber_count(B) == 1
    assert wait_for(lambda: hub.subscriber_count(B) == 0)
