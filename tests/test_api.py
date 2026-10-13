"""REST API tests (task 6.3) using a fresh, isolated app per test."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import create_router, register_exception_handlers
from app.hub import WebSocketHub
from app.service import PotluckService
from app.store import InMemoryBoardStore
from tests.conftest import FIXED_TODAY, VALID_BODY

B = "board-x"


def build_app():
    store = InMemoryBoardStore()
    hub = WebSocketHub()
    service = PotluckService(store, hub, today=lambda: FIXED_TODAY)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(create_router(service, hub))
    return app, hub


@pytest.fixture()
def client():
    app, _hub = build_app()
    with TestClient(app) as c:
        yield c


def add(client, title="Biryani", **extra):
    body = {**VALID_BODY, "title": title, **extra}
    return client.post(f"/board/{B}/items", json=body)


# ---------------------------------------------------------------- add item
def test_add_item_201_shape(client):
    r = add(client, "  Biryani ", image_url="http://img/x.png")
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {
        "id", "board_id", "title", "image_url", "claimed_by", "created_at", "status",
        "added_by", "employee_id", "bring_date",
    }
    assert body["added_by"] == "Asha"
    assert body["employee_id"] == "E1"
    assert body["bring_date"] == "2026-10-12"
    assert body["title"] == "Biryani"
    assert body["board_id"] == B
    assert body["image_url"] == "http://img/x.png"
    assert body["claimed_by"] is None
    assert body["status"] == "Available"


def test_add_item_blank_title_400(client):
    r = add(client, "   ")
    assert r.status_code == 400
    assert "detail" in r.json()
    assert client.get(f"/board/{B}/state").json()["items"] == []


# --------------------------------------------------------------- claim item
def test_claim_200_shape(client):
    item = add(client).json()
    r = client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": " Ravi "})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == item["id"]
    assert body["claimed_by"] == "Ravi"
    assert body["status"] == "Already Taken"


def test_claim_blank_name_400(client):
    item = add(client).json()
    r = client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "  "})
    assert r.status_code == 400


def test_claim_unknown_item_404(client):
    r = client.post(f"/board/{B}/items/nope/claim", json={"name": "Ravi"})
    assert r.status_code == 404
    assert "detail" in r.json()


def test_claim_conflict_409_with_current_claimer(client):
    item = add(client).json()
    client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"})
    r = client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Asha"})
    assert r.status_code == 409
    body = r.json()
    assert body["current_claimer"] == "Ravi"
    assert "detail" in body


# ------------------------------------------------------------- unclaim item
def test_unclaim_200_shape(client):
    item = add(client).json()
    client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"})
    r = client.post(f"/board/{B}/items/{item['id']}/unclaim", json={"name": "RAVI"})
    assert r.status_code == 200
    body = r.json()
    assert body["claimed_by"] is None
    assert body["status"] == "Available"


def test_unclaim_blank_name_400(client):
    item = add(client).json()
    r = client.post(f"/board/{B}/items/{item['id']}/unclaim", json={"name": ""})
    assert r.status_code == 400


def test_unclaim_unknown_item_404(client):
    r = client.post(f"/board/{B}/items/nope/unclaim", json={"name": "Ravi"})
    assert r.status_code == 404


def test_unclaim_by_other_name_403_and_state_unchanged(client):
    item = add(client).json()
    client.post(f"/board/{B}/items/{item['id']}/claim", json={"name": "Ravi"})
    r = client.post(f"/board/{B}/items/{item['id']}/unclaim", json={"name": "Asha"})
    assert r.status_code == 403
    assert "detail" in r.json()
    items = client.get(f"/board/{B}/state").json()["items"]
    assert items[0]["claimed_by"] == "Ravi"


# ----------------------------------------------------------- state / people
def test_state_shape_for_unknown_board(client):
    r = client.get("/board/unknown/state")
    assert r.status_code == 200
    assert r.json() == {"board_id": "unknown", "items": [], "people": []}


def test_state_and_people_shapes(client):
    a = add(client, "Biryani").json()
    b = add(client, "Salad").json()
    add(client, "Cake")
    client.post(f"/board/{B}/items/{a['id']}/claim", json={"name": "Ravi"})
    client.post(f"/board/{B}/items/{b['id']}/claim", json={"name": "ravi"})

    state = client.get(f"/board/{B}/state").json()
    assert set(state) == {"board_id", "items", "people"}
    assert [i["title"] for i in state["items"]] == ["Biryani", "Salad", "Cake"]
    assert len(state["people"]) == 1
    assert state["people"][0]["name"] == "Ravi"
    assert [i["id"] for i in state["people"][0]["items"]] == [a["id"], b["id"]]

    people = client.get(f"/board/{B}/people").json()
    assert set(people) == {"board_id", "people"}
    assert people["board_id"] == B
    assert people["people"] == state["people"]


def test_people_empty_for_unknown_board(client):
    assert client.get("/board/none/people").json() == {"board_id": "none", "people": []}


# ------------------------------------------------------------ app.main app
def test_main_healthz_root_redirect_and_board_page():
    from app.main import app

    with TestClient(app) as c:
        health = c.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        redirect = c.get("/", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"].startswith("/board/")

        page = c.get("/board/x")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
