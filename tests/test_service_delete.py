"""Service tests for strict add validation and ownership-checked delete.

**Validates: Requirements 9.x (add validation), 10.x/11.x (delete)**
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionError as DomainPermissionError,
    ValidationError,
)
from app.models import ChangeType, Item
from tests.conftest import add, make_service

B = "b1"
GOOD = {"title": "Dish", "by": "Asha", "emp": "E1", "date": "2026-10-12"}


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ add validation
BAD_FIELD_VALUES = {
    "title": [None, "", "   ", 5, "x" * 101],
    "by": [None, "", "   ", 5, "x" * 61],
    "emp": [None, "", "   ", 5, "x" * 31],
    "date": [
        None, "", "   ", 20261012, "2026-02-30", "2026-10-5", "20261012",
        "2026-W41-1", "２０２６-10-12", "2026-13-01", "2026-10-12T00:00",
    ],
}  # fmt: skip


@pytest.mark.parametrize("field", list(BAD_FIELD_VALUES))
def test_each_invalid_field_rejected_without_state_change_or_broadcast(field):
    for bad in BAD_FIELD_VALUES[field]:
        service, store, hub = make_service()
        with pytest.raises(ValidationError) as exc:
            add(service, B, **{**GOOD, field: bad})
        if isinstance(bad, str) and len(bad.strip()) > 3:
            assert bad.strip() not in str(exc.value)  # never echoes the value
        assert store.get_board(B) is None  # no lazily created board
        assert store.list_items(B) == []
        assert hub.events == []


def test_values_at_limits_accepted_and_padded_values_stored_trimmed():
    service, store, hub = make_service()
    item = add(service, B, "  " + "t" * 100 + " ", " " + "n" * 60 + "  ", " " + "e" * 30, " 2026-10-12 ")
    stored = store.get_item(B, item.id)
    assert stored.title == "t" * 100
    assert stored.added_by == "n" * 60
    assert stored.employee_id == "e" * 30
    assert stored.bring_date == "2026-10-12"
    assert [e.type for _, e in hub.events] == [ChangeType.ITEM_ADDED]


@pytest.mark.parametrize("date", ["2024-02-29", "0001-01-01", "9999-12-31", "2026-01-01"])
def test_odd_but_real_dates_accepted(date):
    import datetime

    service, _store, _hub = make_service(today=datetime.date(1, 1, 1))
    assert add(service, B, date=date).bring_date == date


# ------------------------------------------------------------ delete matrix
def test_delete_with_matching_id_different_case_and_padding_removes_only_that_item():
    service, store, hub = make_service()
    keep = add(service, B, "Keep", emp="Z9")
    target = add(service, B, "Target", emp="Emp-1")
    deleted = run(service.delete_item(B, target.id, "  eMP-1 "))
    assert deleted.id == target.id
    assert [i.id for i in store.list_items(B)] == [keep.id]
    assert [i.id for i in service.get_board_state(B).items] == [keep.id]
    assert [e.type for _, e in hub.events].count(ChangeType.ITEM_DELETED) == 1
    assert hub.events[-1][1].type == ChangeType.ITEM_DELETED
    assert hub.events[-1][1].item.id == target.id


@pytest.mark.parametrize("submitted", ["E2", "", "   ", None, 1, ["E1"]])
def test_wrong_or_missing_id_is_forbidden_and_item_stays(submitted):
    service, store, hub = make_service()
    item = add(service, B)
    before = len(hub.events)
    with pytest.raises(DomainPermissionError) as exc:
        run(service.delete_item(B, item.id, submitted))
    assert "E1" not in str(exc.value)
    assert store.get_item(B, item.id) is not None
    assert len(hub.events) == before


def test_claimed_with_right_id_conflicts_and_wrong_id_forbidden():
    service, store, hub = make_service()
    item = add(service, B)
    run(service.claim_item(B, item.id, "Ravi"))
    before = len(hub.events)
    with pytest.raises(ConflictError) as exc:
        run(service.delete_item(B, item.id, "e1"))
    assert exc.value.current_claimer == "Ravi"
    assert "release" in str(exc.value)
    with pytest.raises(DomainPermissionError):
        run(service.delete_item(B, item.id, "nope"))
    assert store.get_item(B, item.id).claimed_by == "Ravi"
    assert len(hub.events) == before


def test_delete_after_unclaim_succeeds():
    service, store, _hub = make_service()
    item = add(service, B)
    run(service.claim_item(B, item.id, "Ravi"))
    run(service.unclaim_item(B, item.id, "Ravi"))
    run(service.delete_item(B, item.id, "E1"))
    assert store.get_item(B, item.id) is None


def test_unknown_item_or_board_is_not_found():
    service, _store, hub = make_service()
    item = add(service, B)
    before = len(hub.events)
    with pytest.raises(NotFoundError):
        run(service.delete_item(B, "missing", "E1"))
    with pytest.raises(NotFoundError):
        run(service.delete_item("other-board", item.id, "E1"))
    assert len(hub.events) == before


def _legacy(store, item_id="legacy", claimed_by=None):
    store.save_item(B, Item(item_id, B, "Old", None, claimed_by, 1.0))


@pytest.mark.parametrize("submitted", ["anything", None, ""])
def test_legacy_item_deletable_with_any_submitted_id(submitted):
    service, store, hub = make_service()
    _legacy(store)
    run(service.delete_item(B, "legacy", submitted))
    assert store.get_item(B, "legacy") is None
    assert [e.type for _, e in hub.events] == [ChangeType.ITEM_DELETED]


def test_claimed_legacy_item_conflicts():
    service, store, hub = make_service()
    _legacy(store, claimed_by="Ravi")
    with pytest.raises(ConflictError):
        run(service.delete_item(B, "legacy", "x"))
    assert store.get_item(B, "legacy") is not None
    assert hub.events == []


def test_deleted_item_gone_from_snapshot_and_people_view():
    service, _store, _hub = make_service()
    a = add(service, B, "A")
    b = add(service, B, "B", emp="E2")
    run(service.claim_item(B, a.id, "Ravi"))
    run(service.claim_item(B, b.id, "Ravi"))
    run(service.unclaim_item(B, b.id, "Ravi"))
    run(service.delete_item(B, b.id, "E2"))
    state = service.get_board_state(B)
    assert [i.id for i in state.items] == [a.id]
    assert [i.id for p in state.people for i in p.items] == [a.id]


# --------------------------------------------------------------------- race
def _assert_one_winner(results, store, item_id):
    ok = [r for r in results if not isinstance(r, BaseException)]
    errs = [r for r in results if isinstance(r, BaseException)]
    assert len(ok) == 1 and len(errs) == 1
    assert isinstance(errs[0], (ConflictError, NotFoundError))
    stored = store.get_item(B, item_id)
    if isinstance(errs[0], ConflictError):  # claim won
        assert stored is not None and stored.claimed_by == "Ravi"
    else:  # delete won
        assert stored is None


def test_claim_vs_delete_race_gather():
    for _ in range(20):
        service, store, hub = make_service()
        item = add(service, B)

        async def go():
            return await asyncio.gather(
                service.claim_item(B, item.id, "Ravi"),
                service.delete_item(B, item.id, "E1"),
                return_exceptions=True,
            )

        results = asyncio.run(go())
        _assert_one_winner(results, store, item.id)
        assert len(hub.events) == 2  # add + exactly one winner event


def test_claim_vs_delete_race_threads():
    for _ in range(20):
        service, store, hub = make_service()
        item = add(service, B)
        barrier = threading.Barrier(2)
        results: list = [None, None]

        def worker(idx, make):
            barrier.wait()
            try:
                results[idx] = asyncio.run(make())
            except BaseException as exc:  # noqa: BLE001
                results[idx] = exc

        threads = [
            threading.Thread(target=worker, args=(0, lambda: service.claim_item(B, item.id, "Ravi"))),
            threading.Thread(target=worker, args=(1, lambda: service.delete_item(B, item.id, "E1"))),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)
        _assert_one_winner(results, store, item.id)
        assert len(hub.events) == 2
