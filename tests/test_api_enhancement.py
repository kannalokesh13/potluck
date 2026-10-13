"""API tests for the four-field add body and the delete endpoint.

**Validates: Requirements 9.x, 10.x, 11.x**
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import VALID_BODY
from tests.test_api import build_app

B = "board-d"
SANITIZED = "The request was not valid. Please check the fields and try again."


@pytest.fixture()
def env():
    app, hub = build_app()
    with TestClient(app) as client:
        yield client, hub


def post_item(client, **overrides):
    return client.post(f"/board/{B}/items", json={**VALID_BODY, **overrides})


def delete(client, item_id, **kwargs):
    return client.post(f"/board/{B}/items/{item_id}/delete", **kwargs)


def items(client):
    return client.get(f"/board/{B}/state").json()["items"]


def test_add_returns_201_with_public_fields(env):
    client, _ = env
    r = post_item(client, title=" Biryani ", added_by=" Asha ", employee_id=" E1 ")
    assert r.status_code == 201
    body = r.json()
    assert body["title"] == "Biryani"
    assert body["added_by"] == "Asha"
    assert body["employee_id"] == "E1"
    assert body["bring_date"] == "2026-10-12"


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": None}, {"added_by": ""}, {"employee_id": "  "}, {"bring_date": "2026-02-30"},
        {"title": 5}, {"bring_date": 20261012}, {"employee_id": "x" * 31}, {"added_by": ["a"]},
        {"bring_date": "2026-W41-1"}, {"image_url": 5},
    ],
)  # fmt: skip
def test_invalid_add_bodies_are_400_and_store_nothing(env, overrides):
    client, _ = env
    r = post_item(client, **overrides)
    assert r.status_code == 400
    assert items(client) == []


def test_missing_fields_and_wrong_body_shapes_are_400(env):
    client, _ = env
    assert client.post(f"/board/{B}/items", json={}).status_code == 400
    assert client.post(f"/board/{B}/items", json={"title": "Dish"}).status_code == 400
    assert client.post(f"/board/{B}/items", json=[1, 2]).status_code == 400
    assert items(client) == []


def test_malformed_json_and_claim_shape_errors_get_sanitized_400(env):
    client, _ = env
    r = client.post(
        f"/board/{B}/items",
        content='{"employee_id": "SECRET-ID", ',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json() == {"detail": SANITIZED}
    assert "SECRET-ID" not in r.text

    item = post_item(client).json()
    r = client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": 5})
    assert r.status_code == 400
    assert r.json() == {"detail": SANITIZED}
    r = client.post(f"/board/{B}/items/{item['id']}/claim", json={})
    assert r.status_code == 400


def test_delete_200_returns_deleted_item_and_removes_it(env):
    client, _ = env
    keep = post_item(client, title="Keep", employee_id="K1").json()
    item = post_item(client, title="Gone").json()
    r = delete(client, item["id"], json={"employee_id": " e1 "})
    assert r.status_code == 200
    assert r.json()["id"] == item["id"]
    assert r.json()["employee_id"] == "E1"
    assert [i["id"] for i in items(client)] == [keep["id"]]


def test_delete_403_wrong_missing_and_query_only_id(env):
    client, _ = env
    item = post_item(client).json()
    url = f"/board/{B}/items/{item['id']}/delete"
    wrong = client.post(url, json={"employee_id": "nope"})
    assert wrong.status_code == 403
    assert client.post(url).status_code == 403  # no body
    assert client.post(url, json={}).status_code == 403
    assert client.post(url, json={"employee_id": None}).status_code == 403
    assert client.post(url, json={"employee_id": 7}).status_code == 403
    # Id only in the query string counts as missing.
    assert client.post(url + "?employee_id=E1").status_code == 403
    assert client.post(url + "?employee_id=E1", json={}).status_code == 403
    assert len(items(client)) == 1
    assert "E1" not in wrong.json()["detail"]


def test_delete_409_when_claimed_and_404_when_unknown(env):
    client, _ = env
    item = post_item(client).json()
    client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"})
    r = delete(client, item["id"], json={"employee_id": "E1"})
    assert r.status_code == 409
    assert r.json()["current_claimer"] == "Ravi"
    assert len(items(client)) == 1

    assert delete(client, "missing", json={"employee_id": "E1"}).status_code == 404
    r = client.post("/board/none/items/x/delete", json={"employee_id": "E1"})
    assert r.status_code == 404


def test_websocket_two_sockets_get_exactly_one_item_deleted_frame(env):
    client, hub = env
    with client.websocket_connect(f"/board/{B}/ws") as ws1, client.websocket_connect(
        f"/board/{B}/ws"
    ) as ws2:
        for ws in (ws1, ws2):
            json.loads(ws.receive_text())  # subscribed ack
        item = post_item(client).json()
        for ws in (ws1, ws2):
            assert json.loads(ws.receive_text())["type"] == "ItemAdded"

        # Failed delete: no frame.
        assert delete(client, item["id"], json={"employee_id": "bad"}).status_code == 403
        for ws in (ws1, ws2):
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

        assert delete(client, item["id"], json={"employee_id": "E1"}).status_code == 200
        for ws in (ws1, ws2):
            frame = json.loads(ws.receive_text())
            assert set(frame) == {"type", "board_id", "item"}
            assert frame["type"] == "ItemDeleted"
            assert frame["board_id"] == B
            assert frame["item"]["id"] == item["id"]
            ws.send_text("ping")
            assert ws.receive_text() == "pong"  # exactly one frame
