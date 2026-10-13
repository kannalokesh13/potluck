"""Behavioural tests for PostgresBoardStore against a real database.

Skipped unless ``DATABASE_URL`` is set. Every test uses unique random board ids
and deletes only its own rows afterwards (never TRUNCATE / DROP).
"""

from __future__ import annotations

import asyncio
import datetime
import os
import secrets

import pytest

from app.exceptions import ConflictError
from app.hub import WebSocketHub
from app.models import Item
from app.service import PotluckService

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="DATABASE_URL not set"
)


def _item(item_id: str, board_id: str, title: str = "Dish") -> Item:
    return Item(
        id=item_id,
        board_id=board_id,
        title=title,
        image_url=None,
        claimed_by=None,
        created_at=1.0,
    )


@pytest.fixture(scope="module")
def store():
    from app.pg_store import PostgresBoardStore

    s = PostgresBoardStore(os.environ["DATABASE_URL"])
    yield s
    s.close()


@pytest.fixture
def board_ids(store):
    """Factory for unique board ids; their rows are deleted afterwards."""
    created: list[str] = []

    def make() -> str:
        board_id = "test-" + secrets.token_hex(8)
        created.append(board_id)
        return board_id

    yield make

    with store._connection() as conn:
        for board_id in created:
            conn.execute("DELETE FROM potluck_items WHERE board_id = %s", (board_id,))
            conn.execute("DELETE FROM potluck_boards WHERE id = %s", (board_id,))


def test_create_and_get_board(store, board_ids):
    b = board_ids()
    assert store.get_board(b) is None
    board = store.create_board(b)
    assert board.id == b
    assert store.get_board(b) == board


def test_create_board_is_idempotent_and_keeps_items(store, board_ids):
    b = board_ids()
    first = store.create_board(b)
    store.save_item(b, _item("i1", b))
    second = store.create_board(b)
    assert second == first
    assert [i.id for i in store.list_items(b)] == ["i1"]


def test_save_get_list_items_in_insertion_order(store, board_ids):
    b = board_ids()
    store.create_board(b)
    ids = ["c", "a", "b"]
    for i in ids:
        store.save_item(b, _item(i, b))
    assert [i.id for i in store.list_items(b)] == ids
    got = store.get_item(b, "a")
    assert got is not None and got.id == "a" and got.board_id == b


def test_save_item_lazily_creates_board(store, board_ids):
    b = board_ids()
    store.save_item(b, _item("i1", b))
    assert store.get_board(b) is not None


def test_resaving_item_preserves_order_and_created_at(store, board_ids):
    b = board_ids()
    store.save_item(b, _item("x", b))
    store.save_item(b, _item("y", b))
    updated = _item("x", b, title="Updated")
    updated.claimed_by = "Ravi"
    updated.created_at = 999.0
    store.save_item(b, updated)
    items = store.list_items(b)
    assert [i.id for i in items] == ["x", "y"]
    assert items[0].title == "Updated"
    assert items[0].claimed_by == "Ravi"
    assert items[0].created_at == 1.0


def test_unknown_board_and_item_return_none_or_empty(store, board_ids):
    b = board_ids()
    assert store.get_board(b) is None
    assert store.get_item(b, "i") is None
    assert store.list_items(b) == []
    store.create_board(b)
    assert store.get_item(b, "missing") is None
    assert store.list_items(b) == []


def test_items_are_scoped_per_board(store, board_ids):
    b1, b2 = board_ids(), board_ids()
    store.save_item(b1, _item("i1", b1))
    store.save_item(b2, _item("i2", b2))
    assert store.get_item(b1, "i2") is None
    assert [i.id for i in store.list_items(b2)] == ["i2"]


def test_get_item_returns_new_object_each_time(store, board_ids):
    b = board_ids()
    store.save_item(b, _item("i1", b))
    first = store.get_item(b, "i1")
    first.claimed_by = "Mutated"  # not saved
    assert store.get_item(b, "i1").claimed_by is None


def test_item_lock_same_for_same_item_different_for_others(store):
    assert store.item_lock("b1", "i1") is store.item_lock("b1", "i1")
    assert store.item_lock("b1", "i1") is not store.item_lock("b1", "i2")
    assert store.item_lock("b1", "i1") is not store.item_lock("b2", "i1")


def test_claim_flow_through_service(store, board_ids):
    b = board_ids()
    service = PotluckService(store, WebSocketHub(), today=lambda: datetime.date(2000, 1, 1))

    item = asyncio.run(service.add_item(b, "  Biryani  ", "Asha", "E1", "2026-10-12", None))
    assert item.title == "Biryani"
    assert [i.id for i in service.get_board_state(b).items] == [item.id]

    claimed = asyncio.run(service.claim_item(b, item.id, "Ravi"))
    assert claimed.claimed_by == "Ravi"

    with pytest.raises(ConflictError):
        asyncio.run(service.claim_item(b, item.id, "Asha"))

    people = service.list_people(b)
    assert [p.name for p in people] == ["Ravi"]
    assert [i.id for i in people[0].items] == [item.id]

    released = asyncio.run(service.unclaim_item(b, item.id, "ravi"))
    assert released.claimed_by is None
    assert service.list_people(b) == []
    assert store.get_item(b, item.id).claimed_by is None


def test_data_persists_across_new_store_instance(store, board_ids):
    from app.pg_store import PostgresBoardStore

    b = board_ids()
    service = PotluckService(store, WebSocketHub(), today=lambda: datetime.date(2000, 1, 1))
    item = asyncio.run(service.add_item(b, "Samosa", "Asha", "E1", "2026-10-12", "http://img/x.png"))
    asyncio.run(service.claim_item(b, item.id, "Meena"))

    other = PostgresBoardStore(os.environ["DATABASE_URL"])
    try:
        assert other.get_board(b) is not None
        items = other.list_items(b)
        assert [i.id for i in items] == [item.id]
        assert items[0].title == "Samosa"
        assert items[0].image_url == "http://img/x.png"
        assert items[0].claimed_by == "Meena"
    finally:
        other.close()


def test_delete_expired_removes_only_its_own_expired_rows(store, board_ids):
    """Uses a 2000-01-01 cutoff and its own board so real data is never matched."""
    b = board_ids()

    def dated(item_id, bring_date):
        item = _item(item_id, b)
        item.bring_date = bring_date
        return item

    store.save_item(b, dated("old", "1999-12-31"))
    store.save_item(b, dated("edge", "2000-01-01"))
    store.save_item(b, dated("legacy", None))

    removed = store.delete_expired("2000-01-01")

    mine = [i for i in removed if i.board_id == b]
    assert [i.id for i in mine] == ["old"]
    assert mine[0].bring_date == "1999-12-31"
    assert sorted(i.id for i in store.list_items(b)) == ["edge", "legacy"]
