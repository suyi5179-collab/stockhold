"""Reserve, confirm, release against real DynamoDB (LocalStack), including the oversell race."""
import time

import pytest

from stockhold import inventory
from stockhold.states import (CONFIRMED, HELD, RELEASED, Contention, HoldExpired, InsufficientStock,
                              InvalidTransition, NotFound)

from .conftest import run_concurrently


def test_reserve_confirm_moves_units(new_sku):
    sku = new_sku(5)
    h = inventory.reserve(sku, 2, "o-1")
    assert h["status"] == HELD and h["qty"] == 2
    assert h["reserve"]["available_before"] == 5 and h["reserve"]["available_after"] == 3
    assert h["reserve"]["version_after"] == h["reserve"]["version_before"] + 1
    r = inventory.confirm(h["hold_id"])
    assert r["changed"] and r["hold"]["status"] == CONFIRMED
    assert [e["to"] for e in r["hold"]["history"]] == [HELD, CONFIRMED]
    rep = inventory.stock_report(sku)
    assert (rep["available"], rep["held"], rep["confirmed"], rep["balanced"]) == (3, 0, 2, True)


def test_release_returns_units(new_sku):
    sku = new_sku(5)
    h = inventory.reserve(sku, 4, "o-1")
    r = inventory.release(h["hold_id"], reason="payment failed")
    assert r["changed"] and r["hold"]["status"] == RELEASED
    assert inventory.stock_report(sku)["available"] == 5


def test_insufficient_stock_writes_nothing(new_sku):
    sku = new_sku(2)
    with pytest.raises(InsufficientStock) as e:
        inventory.reserve(sku, 3, "o-1")
    assert e.value.detail == {"requested": 3, "available": 2}
    rep = inventory.stock_report(sku)
    assert rep["available"] == 2 and rep["holds"] == 0


def test_unknown_sku():
    with pytest.raises(NotFound):
        inventory.reserve("no-such-sku", 1, "o-1")


def test_terminal_hold_cannot_move(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 1, "o-1")
    inventory.confirm(h["hold_id"])
    with pytest.raises(InvalidTransition) as e:
        inventory.release(h["hold_id"])
    assert e.value.detail["from_state"] == CONFIRMED
    assert inventory.stock_report(sku)["available"] == 2


def test_confirm_after_expiry_is_refused(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 1, "o-1", hold_seconds=1)
    time.sleep(1.2)
    with pytest.raises(HoldExpired):
        inventory.confirm(h["hold_id"])
    assert inventory.get_hold(h["hold_id"])["status"] == HELD  # the expirer, not confirm, moves it


# @learn:test-oversell
def test_concurrent_checkouts_never_oversell(new_sku):
    """30 buyers race for 10 units on one shard: exactly the units in stock are held, never more."""
    sku = new_sku(10)
    results = run_concurrently(30, lambda i: inventory.reserve(sku, 1, f"o-{i}"))
    held = [r for r in results if isinstance(r, dict)]
    unexpected = [r for r in results if not isinstance(r, (dict, InsufficientStock, Contention))]
    assert not unexpected, unexpected
    rep = inventory.stock_report(sku)
    assert rep["oversold"] == 0, rep
    assert len(held) <= 10 and rep["held"] == len(held)
    assert rep["balanced"] and not rep["negative_shards"]
    # Contention means "retry later", not "sold out": anyone who got it left stock behind.
    if len(held) < 10:
        assert any(isinstance(r, Contention) for r in results)
# @learn:end test-oversell
