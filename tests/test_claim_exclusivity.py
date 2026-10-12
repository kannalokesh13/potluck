"""Property 3: claim exclusivity under concurrency (task 4.5).

**Validates: Requirements 2.2**
"""

from __future__ import annotations

import asyncio
import threading

from app.exceptions import ConflictError
from tests.conftest import make_service

B = "b1"
ROUNDS = 10
N = 20


def _setup():
    service, store, hub = make_service()
    item = asyncio.run(service.add_item(B, "Biryani"))
    return service, store, hub, item


def _assert_single_winner(results, names, store, hub, item):
    winners = [(n, r) for n, r in zip(names, results) if not isinstance(r, BaseException)]
    losers = [r for r in results if isinstance(r, BaseException)]
    assert len(winners) == 1
    assert len(losers) == len(names) - 1
    assert all(isinstance(e, ConflictError) for e in losers)
    winner_name = winners[0][0]
    assert store.get_item(B, item.id).claimed_by == winner_name
    # The conflict reports the actual winner.
    assert all(e.current_claimer == winner_name for e in losers)
    # One ItemAdded + exactly one ItemClaimed.
    assert len(hub.events) == 2


def test_gather_concurrent_claims_single_winner():
    for _ in range(ROUNDS):
        service, store, hub, item = _setup()
        names = [f"user{i}" for i in range(N)]

        async def go():
            return await asyncio.gather(
                *(service.claim_item(B, item.id, n) for n in names),
                return_exceptions=True,
            )

        results = asyncio.run(go())
        _assert_single_winner(results, names, store, hub, item)


def test_threaded_concurrent_claims_single_winner():
    for _ in range(ROUNDS):
        service, store, hub, item = _setup()
        names = [f"user{i}" for i in range(N)]
        barrier = threading.Barrier(N)
        results: list = [None] * N

        def worker(idx: int) -> None:
            barrier.wait()
            try:
                results[idx] = asyncio.run(service.claim_item(B, item.id, names[idx]))
            except BaseException as exc:  # noqa: BLE001 - recorded for assertions
                results[idx] = exc

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads)

        _assert_single_winner(results, names, store, hub, item)
