"""Property-based tests over random add/claim/unclaim sequences.

Covers design Properties 1, 2, 4, 5 and 8 (tasks 4.2, 4.4, 4.7, 4.9).

Each hypothesis example builds a brand new service/store/hub, applies a random
sequence of operations, and runs a property-specific check after every step.
Expected domain exceptions are absorbed by the op runner and handed to the
checker so failed operations are verified too.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any, Callable

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionError as DomainPermissionError,
    PotluckError,
    ValidationError,
)
from app.models import Item, ItemStatus
from tests.conftest import make_service

BOARD = "board-1"

SETTINGS = settings(
    max_examples=75,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# Names include case/whitespace variants of the same person and blanks.
NAMES = ["Ravi", "ravi", "  RAVI ", "Asha", "bob", "Bob", "", "   "]
titles = st.text(alphabet="abc XYZ", max_size=6)

ops = st.one_of(
    st.tuples(st.just("add"), titles),
    st.tuples(st.just("claim"), st.integers(0, 6), st.sampled_from(NAMES)),
    st.tuples(st.just("unclaim"), st.integers(0, 6), st.sampled_from(NAMES)),
)
op_sequences = st.lists(ops, max_size=25)


@dataclass
class Step:
    """Outcome of one applied operation, passed to property checkers."""

    op: tuple
    before: list[Item]  # deep-ish copies of all items prior to the op
    after: list[Item]  # copies of all items after the op
    result: Item | None  # returned item on success
    error: PotluckError | None  # domain exception on failure
    target_before: Item | None  # copy of the targeted item before the op
    service: Any


def _copies(items: list[Item]) -> list[Item]:
    # The store hands out live objects the service mutates in place, so take
    # value copies for before/after comparisons.
    return [replace(i) for i in items]


def run_ops(sequence: list[tuple], check: Callable[[Step], None]) -> None:
    """Apply ``sequence`` to a fresh service, calling ``check`` after each op."""
    service, store, _hub = make_service()

    for op in sequence:
        before = _copies(store.list_items(BOARD))
        target_before = None
        result = None
        error = None

        if op[0] == "add":
            coro = service.add_item(BOARD, op[1])
        else:
            if not before:
                continue  # nothing to claim/unclaim yet
            target = before[op[1] % len(before)]
            target_before = target
            if op[0] == "claim":
                coro = service.claim_item(BOARD, target.id, op[2])
            else:
                coro = service.unclaim_item(BOARD, target.id, op[2])

        try:
            result = asyncio.run(coro)
        except (ValidationError, NotFoundError, ConflictError, DomainPermissionError) as exc:
            error = exc

        after = _copies(store.list_items(BOARD))
        check(Step(op, before, after, result, error, target_before, service))


def _by_id(items: list[Item]) -> dict[str, Item]:
    return {i.id: i for i in items}


# ----------------------------------------------------------------------
# Property 1: status consistency
# ----------------------------------------------------------------------
@SETTINGS
@given(op_sequences)
def test_property_1_status_consistency(sequence):
    """**Validates: Requirements 4.1**"""

    def check(step: Step) -> None:
        for item in step.after:
            taken = bool(item.claimed_by)
            assert (item.status == ItemStatus.ALREADY_TAKEN) == taken
            assert (item.status == ItemStatus.AVAILABLE) == (not taken)

    run_ops(sequence, check)


# ----------------------------------------------------------------------
# Property 2: single claimer
# ----------------------------------------------------------------------
@SETTINGS
@given(op_sequences)
def test_property_2_single_claimer(sequence):
    """**Validates: Requirements 2.1**"""

    def check(step: Step) -> None:
        for item in step.after:
            assert item.claimed_by is None or (
                isinstance(item.claimed_by, str)
                and item.claimed_by != ""
                and item.claimed_by == item.claimed_by.strip()
            )
        # A claim never overwrites an existing claimer. A blank name is rejected
        # by validation before the conflict check, so either error is correct.
        if step.op[0] == "claim" and step.target_before.claimed_by is not None:
            assert isinstance(step.error, (ConflictError, ValidationError))
            assert (
                _by_id(step.after)[step.target_before.id].claimed_by
                == step.target_before.claimed_by
            )

    run_ops(sequence, check)


# ----------------------------------------------------------------------
# Property 4: add preserves others
# ----------------------------------------------------------------------
@SETTINGS
@given(op_sequences)
def test_property_4_add_preserves_others(sequence):
    """**Validates: Requirements 1.1**"""

    def check(step: Step) -> None:
        if step.op[0] != "add":
            return
        if step.error is not None:
            # Blank title: rejected, nothing changed.
            assert isinstance(step.error, ValidationError)
            assert step.after == step.before
            return
        assert len(step.after) == len(step.before) + 1
        # Existing items unchanged and in the same order.
        assert step.after[:-1] == step.before
        new = step.after[-1]
        assert new.status == ItemStatus.AVAILABLE
        assert new.claimed_by is None
        assert new.id not in {i.id for i in step.before}
        assert new.title == step.op[1].strip()

    run_ops(sequence, check)


# ----------------------------------------------------------------------
# Property 8: unclaim ownership
# ----------------------------------------------------------------------
@SETTINGS
@given(op_sequences)
def test_property_8_unclaim_ownership(sequence):
    """**Validates: Requirements 2.3, 2.4**"""

    def check(step: Step) -> None:
        if step.op[0] != "unclaim":
            return
        name = step.op[2].strip()
        before = step.target_before
        owner = (before.claimed_by or "").strip()

        if not name:
            assert isinstance(step.error, ValidationError)
            assert step.after == step.before
        elif owner and owner.lower() == name.lower():
            assert step.error is None
            now = _by_id(step.after)[before.id]
            assert now.claimed_by is None
            assert now.status == ItemStatus.AVAILABLE
            # Every other item is untouched.
            assert [i for i in step.after if i.id != before.id] == [
                i for i in step.before if i.id != before.id
            ]
        else:
            assert isinstance(step.error, DomainPermissionError)
            assert step.after == step.before  # state unchanged

    run_ops(sequence, check)


# ----------------------------------------------------------------------
# Property 5: people-view completeness and soundness
# ----------------------------------------------------------------------
@SETTINGS
@given(op_sequences)
def test_property_5_people_view(sequence):
    """**Validates: Requirements 5.1**"""

    def check(step: Step) -> None:
        people = step.service.list_people(BOARD)
        flattened = [i.id for p in people for i in p.items]

        claimed_ids = [i.id for i in step.after if i.claimed_by]
        # Completeness + soundness: same set, and no item listed twice.
        assert set(flattened) == set(claimed_ids)
        assert len(flattened) == len(claimed_ids)
        unclaimed = {i.id for i in step.after if not i.claimed_by}
        assert unclaimed.isdisjoint(flattened)

        # One entry per case-insensitive distinct name.
        keys = [p.name.strip().lower() for p in people]
        assert len(keys) == len(set(keys))
        assert set(keys) == {i.claimed_by.strip().lower() for i in step.after if i.claimed_by}

        # Each item sits under the person who claimed it.
        for p in people:
            assert p.items
            for item in p.items:
                assert item.claimed_by.strip().lower() == p.name.strip().lower()

    run_ops(sequence, check)
