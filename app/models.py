"""Core data models for the Realtime Potluck Planner.

These models follow the design document's "Data Models" section. The single
source of truth for an item's status is its ``claimed_by`` field; ``status`` is
always derived, never stored independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ItemStatus(str, Enum):
    """Derived status of an item.

    AVAILABLE: no one has claimed the item (``claimed_by`` is unset).
    ALREADY_TAKEN: claimed by exactly one participant.
    """

    AVAILABLE = "Available"
    ALREADY_TAKEN = "Already Taken"


@dataclass
class Item:
    """A dish to bring to the potluck.

    Attributes:
        id: Unique identifier within the owning board.
        board_id: Identifier of the board this item belongs to.
        title: Display title (e.g., "Biryani"); non-empty after trimming.
        image_url: Optional food image reference, or None.
        claimed_by: Participant display name holding the item, or None.
        created_at: Creation time in epoch seconds.
        added_by: Public name of the adder; None on legacy items.
        employee_id: Public employee id shown on the card and used to authorize
            delete (``repr=False`` keeps it out of reprs); None on legacy items.
        bring_date: Public ISO ``YYYY-MM-DD`` string; None on legacy items.
    """

    id: str
    board_id: str
    title: str
    image_url: str | None
    claimed_by: str | None
    created_at: float
    added_by: str | None = None
    employee_id: str | None = field(default=None, repr=False)
    bring_date: str | None = None

    @property
    def status(self) -> ItemStatus:
        """Derive the status from ``claimed_by``.

        An item is ALREADY_TAKEN when ``claimed_by`` is a non-empty name,
        otherwise AVAILABLE. A whitespace-only name is treated as unclaimed.
        """
        if self.claimed_by and self.claimed_by.strip():
            return ItemStatus.ALREADY_TAKEN
        return ItemStatus.AVAILABLE


@dataclass
class Board:
    """A single shared potluck context identified by an unguessable id."""

    id: str
    created_at: float


@dataclass
class PersonContribution:
    """A participant together with the items they are bringing."""

    name: str
    items: list[Item] = field(default_factory=list)


@dataclass
class BoardState:
    """Full snapshot of a board: all items plus grouped people."""

    board_id: str
    items: list[Item]
    people: list[PersonContribution]


class ChangeType(str, Enum):
    """Type of realtime change event broadcast after a successful mutation."""

    ITEM_ADDED = "ItemAdded"
    ITEM_CLAIMED = "ItemClaimed"
    ITEM_UNCLAIMED = "ItemUnclaimed"
    ITEM_DELETED = "ItemDeleted"


@dataclass
class ChangeEvent:
    """A realtime payload describing a single successful mutation."""

    type: ChangeType
    board_id: str
    item: Item
