"""Retries and duplicate messages: the same request twice must not take or give back stock twice."""
import pytest

from stockhold import inventory
from stockhold.states import RELEASED, IdempotencyKeyReused

from .conftest import run_concurrently


# @learn:test-unique-keys
def test_same_key_returns_the_same_hold(new_sku):
    sku = new_sku(5)
    # The key carries this run's sku: the table outlives the test run, and an old hold with the same
    # key and a different body is (correctly) a 422.
    a = inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-1-{sku}")
    b = inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-1-{sku}")
    assert a["hold_id"] == b["hold_id"] and b["replayed"] is True and "replayed" not in a
    assert inventory.stock_report(sku)["available"] == 3
# @learn:end test-unique-keys


def test_same_key_different_request_is_refused(new_sku):
    sku = new_sku(5)
    inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-2-{sku}")
    with pytest.raises(IdempotencyKeyReused):
        inventory.reserve(sku, 3, "o-1", idempotency_key=f"k-2-{sku}")
    assert inventory.stock_report(sku)["available"] == 3


def test_keys_are_scoped_per_client(new_sku):
    sku = new_sku(5)
    a = inventory.reserve(sku, 1, "o-1", client="shop-a", idempotency_key=f"k-{sku}")
    b = inventory.reserve(sku, 1, "o-1", client="shop-b", idempotency_key=f"k-{sku}")
    assert a["hold_id"] != b["hold_id"]


def test_concurrent_retries_of_one_request_take_stock_once(new_sku):
    sku = new_sku(10)
    results = run_concurrently(10, lambda i: inventory.reserve(sku, 3, "o-1", idempotency_key=f"k-race-{sku}"))
    assert all(isinstance(r, dict) for r in results), results
    assert len({r["hold_id"] for r in results}) == 1
    rep = inventory.stock_report(sku)
    assert rep["available"] == 7 and rep["holds"] == 1 and rep["balanced"]


def test_sold_out_retry_still_finds_its_hold(new_sku):
    sku = new_sku(2)
    a = inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-last-{sku}")
    assert inventory.stock_report(sku)["available"] == 0
    assert inventory.reserve(sku, 2, "o-1", idempotency_key=f"k-last-{sku}")["hold_id"] == a["hold_id"]


def test_release_twice_is_a_noop(new_sku):
    sku = new_sku(5)
    h = inventory.reserve(sku, 2, "o-1")
    assert inventory.release(h["hold_id"])["changed"] is True
    again = inventory.release(h["hold_id"])
    assert again["changed"] is False and again["hold"]["status"] == RELEASED
    assert inventory.stock_report(sku)["available"] == 5


# @learn:test-duplicate-release
def test_duplicate_payment_failures_release_once(new_sku):
    """The same 'payment failed' message delivered 8 times at once gives the units back exactly once."""
    sku = new_sku(5)
    h = inventory.reserve(sku, 2, "o-1")
    results = run_concurrently(8, lambda i: inventory.release(h["hold_id"], by=f"dup-{i}"))
    changed = [r for r in results if isinstance(r, dict) and r["changed"]]
    rep = inventory.stock_report(sku)
    assert rep["available"] == 5, rep          # not 5 + 2 * (copies that slipped through)
    assert rep["balanced"] and len(changed) == 1
    assert [e["to"] for e in inventory.get_hold(h["hold_id"])["history"]].count(RELEASED) == 1
# @learn:end test-duplicate-release
