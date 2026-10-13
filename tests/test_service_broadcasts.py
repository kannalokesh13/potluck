"""Property 6 (realtime fan-out) at the service level plus validation (task 4.10).

**Validates: Requirements 3.1, 7.1, 7.2, 7.3**
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionError as DomainPermissionError,
    ValidationError,
)
from app.models import ChangeType
from tests.conftest import FakeConnection, make_service

B = "b1"


def run(coro):
    return asyncio.run(coro)


def new_item(service, title="Dish"):
    """Coroutine adding a valid item (employee E1, Asha, 2026-10-12)."""
    return service.add_item(B, title, "Asha", "E1", "2026-10-12")


def _fresh():
    service, store, hub = make_service()
    return service, store, hub


def test_exactly_one_event_per_successful_mutation():
    service, _store, hub = _fresh()
    conn = FakeConnection()
    hub.subscribe(B, conn)

    async def scenario():
        item = await new_item(service, "Biryani")
        assert [e.type for _, e in hub.events] == [ChangeType.ITEM_ADDED]
        await service.claim_item(B, item.id, "Ravi")
        assert [e.type for _, e in hub.events] == [
            ChangeType.ITEM_ADDED,
            ChangeType.ITEM_CLAIMED,
        ]
        await service.unclaim_item(B, item.id, "ravi")
        return item

    item = run(scenario())

    assert [e.type for _, e in hub.events] == [
        ChangeType.ITEM_ADDED,
        ChangeType.ITEM_CLAIMED,
        ChangeType.ITEM_UNCLAIMED,
    ]
    assert all(bid == B and e.board_id == B and e.item.id == item.id for bid, e in hub.events)
    # Subscriber received the same three payloads.
    assert len(conn.sent) == 3


def test_no_event_after_failures():
    service, _store, hub = _fresh()

    async def scenario():
        item = await new_item(service, "Biryani")
        await service.claim_item(B, item.id, "Ravi")
        baseline = len(hub.events)

        with pytest.raises(ValidationError):
            await new_item(service, "   ")
        with pytest.raises(ValidationError):
            await service.claim_item(B, item.id, "  ")
        with pytest.raises(ValidationError):
            await service.unclaim_item(B, item.id, "")
        with pytest.raises(NotFoundError):
            await service.claim_item(B, "missing", "Asha")
        with pytest.raises(NotFoundError):
            await service.unclaim_item(B, "missing", "Asha")
        with pytest.raises(ConflictError) as conflict:
            await service.claim_item(B, item.id, "Asha")
        assert conflict.value.current_claimer == "Ravi"
        with pytest.raises(DomainPermissionError):
            await service.unclaim_item(B, item.id, "Asha")
        return baseline

    baseline = run(scenario())
    assert len(hub.events) == baseline == 2


@settings(max_examples=50, deadline=None)
@given(st.lists(st.sampled_from(["add", "claim", "claim2", "unclaim", "unclaim2", "bad"]), max_size=20))
def test_event_count_equals_successful_mutations(steps):
    """Property 6: events == successful mutations, regardless of failures."""
    service, store, hub = _fresh()

    async def scenario():
        successes = 0
        item_id = None
        for step in steps:
            try:
                if step == "add":
                    item_id = (await new_item(service)).id
                elif step == "bad":
                    await new_item(service, " ")
                elif item_id is None:
                    continue
                elif step == "claim":
                    await service.claim_item(B, item_id, "Ravi")
                elif step == "claim2":
                    await service.claim_item(B, item_id, "Asha")
                elif step == "unclaim":
                    await service.unclaim_item(B, item_id, "Ravi")
                elif step == "unclaim2":
                    await service.unclaim_item(B, item_id, "Asha")
                successes += 1
            except (ValidationError, NotFoundError, ConflictError, DomainPermissionError):
                pass
        return successes

    successes = run(scenario())
    assert len(hub.events) == successes


def test_whitespace_title_rejected_and_nothing_stored():
    service, store, hub = _fresh()
    for bad in ["", "   ", "\t\n "]:
        with pytest.raises(ValidationError):
            run(new_item(service, bad))
    assert store.list_items(B) == []
    assert hub.events == []


def test_whitespace_name_rejected_for_claim_and_unclaim():
    service, store, hub = _fresh()
    item = run(new_item(service, "Biryani"))
    for bad in ["", "  ", "\t"]:
        with pytest.raises(ValidationError):
            run(service.claim_item(B, item.id, bad))
        with pytest.raises(ValidationError):
            run(service.unclaim_item(B, item.id, bad))
    assert store.get_item(B, item.id).claimed_by is None
    assert len(hub.events) == 1  # only the original add


def test_trimmed_values_are_stored():
    service, store, _hub = _fresh()
    item = run(new_item(service, "  Biryani  "))
    assert item.title == "Biryani"
    assert store.get_item(B, item.id).title == "Biryani"

    claimed = run(service.claim_item(B, item.id, "  Ravi  "))
    assert claimed.claimed_by == "Ravi"
    assert store.get_item(B, item.id).claimed_by == "Ravi"
