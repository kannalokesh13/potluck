"""Unit tests for InMemoryBoardStore (task 2.2)."""

from __future__ import annotations

from app.models import Item
from app.store import InMemoryBoardStore, generate_board_id, generate_item_id


def _item(item_id: str, board_id: str = "b1", title: str = "Dish") -> Item:
    return Item(
        id=item_id,
        board_id=board_id,
        title=title,
        image_url=None,
        claimed_by=None,
        created_at=1.0,
    )


def test_create_and_get_board():
    store = InMemoryBoardStore()
    assert store.get_board("b1") is None
    board = store.create_board("b1")
    assert board.id == "b1"
    assert store.get_board("b1") is board


def test_create_board_is_idempotent_and_keeps_items():
    store = InMemoryBoardStore()
    first = store.create_board("b1")
    store.save_item("b1", _item("i1"))
    second = store.create_board("b1")
    assert second is first
    assert [i.id for i in store.list_items("b1")] == ["i1"]


def test_save_get_list_items_in_insertion_order():
    store = InMemoryBoardStore()
    store.create_board("b1")
    ids = ["c", "a", "b"]
    for i in ids:
        store.save_item("b1", _item(i))
    assert [i.id for i in store.list_items("b1")] == ids
    assert store.get_item("b1", "a").id == "a"


def test_resaving_item_preserves_order():
    store = InMemoryBoardStore()
    store.save_item("b1", _item("x"))
    store.save_item("b1", _item("y"))
    updated = _item("x", title="Updated")
    store.save_item("b1", updated)
    items = store.list_items("b1")
    assert [i.id for i in items] == ["x", "y"]
    assert items[0].title == "Updated"


def test_unknown_board_and_item_return_none_or_empty():
    store = InMemoryBoardStore()
    assert store.get_board("nope") is None
    assert store.get_item("nope", "i") is None
    assert store.list_items("nope") == []
    store.create_board("b1")
    assert store.get_item("b1", "missing") is None
    assert store.list_items("b1") == []


def test_items_are_scoped_per_board():
    store = InMemoryBoardStore()
    store.save_item("b1", _item("i1", "b1"))
    store.save_item("b2", _item("i2", "b2"))
    assert store.get_item("b1", "i2") is None
    assert [i.id for i in store.list_items("b2")] == ["i2"]


def test_generated_ids_are_unique():
    item_ids = {generate_item_id() for _ in range(500)}
    board_ids = {generate_board_id() for _ in range(500)}
    assert len(item_ids) == 500
    assert len(board_ids) == 500


def _new_item(item_id: str, board_id: str = "b1") -> Item:
    return Item(
        id=item_id,
        board_id=board_id,
        title="Dish",
        image_url=None,
        claimed_by=None,
        created_at=1.0,
        added_by="Asha",
        employee_id="E1",
        bring_date="2026-10-12",
    )


def test_delete_item_true_then_false_and_removes_from_reads():
    store = InMemoryBoardStore()
    store.save_item("b1", _item("i1"))
    assert store.delete_item("b1", "i1") is True
    assert store.delete_item("b1", "i1") is False
    assert store.get_item("b1", "i1") is None
    assert store.list_items("b1") == []


def test_delete_item_unknown_board_or_item_returns_false():
    store = InMemoryBoardStore()
    assert store.delete_item("nope", "i1") is False
    store.save_item("b1", _item("i1"))
    assert store.delete_item("b1", "missing") is False
    assert [i.id for i in store.list_items("b1")] == ["i1"]


def test_delete_item_preserves_order_and_other_boards():
    store = InMemoryBoardStore()
    for i in ["a", "b", "c", "d"]:
        store.save_item("b1", _item(i))
    store.save_item("b2", _item("b", "b2"))

    assert store.delete_item("b1", "b") is True

    assert [i.id for i in store.list_items("b1")] == ["a", "c", "d"]
    assert [i.id for i in store.list_items("b2")] == ["b"]


def test_delete_item_keeps_item_lock_reusable():
    store = InMemoryBoardStore()
    store.save_item("b1", _item("i1"))
    lock = store.item_lock("b1", "i1")
    with lock:
        assert store.delete_item("b1", "i1") is True
    assert store.item_lock("b1", "i1") is lock
    with store.item_lock("b1", "i1"):
        pass


def test_new_fields_round_trip_through_save_get_list():
    store = InMemoryBoardStore()
    store.save_item("b1", _new_item("i1"))
    for item in (store.get_item("b1", "i1"), store.list_items("b1")[0]):
        assert item.added_by == "Asha"
        assert item.employee_id == "E1"
        assert item.bring_date == "2026-10-12"


def test_legacy_item_reads_back_with_none_fields():
    store = InMemoryBoardStore()
    store.save_item("b1", _item("i1"))
    for item in (store.get_item("b1", "i1"), store.list_items("b1")[0]):
        assert item.added_by is None
        assert item.employee_id is None
        assert item.bring_date is None


def test_item_lock_same_for_same_item_different_for_others():
    store = InMemoryBoardStore()
    assert store.item_lock("b1", "i1") is store.item_lock("b1", "i1")
    assert store.item_lock("b1", "i1") is not store.item_lock("b1", "i2")
    assert store.item_lock("b1", "i1") is not store.item_lock("b2", "i1")
