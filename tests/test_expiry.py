"""Expired holds: the delayed message through the real Lambda, and the sweeper as a backstop."""
import json
import time

from stockhold import expiry, inventory
from stockhold.aws import queue_url, sqs
from stockhold.config import settings
from stockhold.states import CONFIRMED, EXPIRED, HELD

from .conftest import run_concurrently


def wait_status(hold_id, want, timeout=45):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = inventory.get_hold(hold_id)
        if h["status"] == want:
            return h, time.time() - t0
        time.sleep(0.5)
    raise AssertionError(f"{hold_id} still {inventory.get_hold(hold_id)['status']} after {timeout}s, wanted {want}")


# @learn:test-expiry-e2e
def test_delayed_message_expires_the_hold_through_lambda(new_sku):
    sku = new_sku(4)
    h = inventory.reserve(sku, 3, "o-1", hold_seconds=3)
    msg = expiry.schedule(h)
    assert msg["delay_seconds"] == 3 and msg["body"]["hold_id"] == h["hold_id"]
    h2, _ = wait_status(h["hold_id"], EXPIRED)
    assert h2["history"][-1]["by"] == "expiry-message"
    lag_ms = h2["history"][-1]["at_ms"] - h2["expires_at_ms"]
    assert 0 <= lag_ms < 30_000, lag_ms        # eventual: after expiresAt, never before
    assert inventory.stock_report(sku)["available"] == 4
# @learn:end test-expiry-e2e


def test_message_for_a_paid_hold_does_nothing(new_sku):
    sku = new_sku(2)
    h = inventory.reserve(sku, 1, "o-1", hold_seconds=30)
    inventory.confirm(h["hold_id"])
    assert expiry.handle_message({"hold_id": h["hold_id"]}) == "already-confirmed"
    assert inventory.get_hold(h["hold_id"])["status"] == CONFIRMED


def test_early_message_is_rescheduled_not_applied(new_sku):
    sku = new_sku(2)
    h = inventory.reserve(sku, 1, "o-1", hold_seconds=600)
    assert expiry.handle_message({"hold_id": h["hold_id"]}) == "rescheduled"
    assert inventory.get_hold(h["hold_id"])["status"] == HELD
    assert expiry.handle_message({"hold_id": "h_does_not_exist"}) == "missing"


def test_duplicate_expiry_messages_return_units_once(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 2, "o-1", hold_seconds=1)
    time.sleep(1.1)
    outcomes = run_concurrently(6, lambda i: expiry.handle_message({"hold_id": h["hold_id"]}))
    assert outcomes.count("expired") == 1, outcomes
    assert inventory.stock_report(sku)["available"] == 3


# @learn:test-sweeper
def test_sweeper_expires_a_hold_whose_message_was_lost(new_sku):
    """Partial failure: the reserve committed, the expiry message was never sent."""
    sku = new_sku(2)
    h = inventory.reserve(sku, 2, "o-1", hold_seconds=1)   # no expiry.schedule(): the message is lost
    assert h["hold_id"] not in {x["hold_id"] for x in expiry.overdue()}
    later = inventory.now_ms() + 1000 + settings().sweep_grace_ms + 1
    assert h["hold_id"] in {x["hold_id"] for x in expiry.overdue(later)}
    r = expiry.sweep(later)
    assert r["expired"] >= 1
    got = inventory.get_hold(h["hold_id"])
    assert got["status"] == EXPIRED and got["history"][-1]["by"] == "sweeper"
    assert inventory.stock_report(sku)["available"] == 2
    assert h["hold_id"] not in {x["hold_id"] for x in expiry.overdue(later)}   # left the sparse index
# @learn:end test-sweeper


def test_api_retry_heals_a_missing_expiry_message(new_sku):
    from stockhold.app import create_app
    sku = new_sku(2)
    c = create_app().test_client()
    hold = inventory.reserve(sku, 1, "o-9", idempotency_key=f"heal-{sku}")   # committed, never scheduled
    assert "expiry_message" not in hold
    r = c.post("/v1/reservations", json={"sku": sku, "qty": 1, "order_id": "o-9"},
               headers={"Idempotency-Key": f"heal-{sku}"})
    assert r.status_code == 200 and r.json["expiry_message"]["body"]["hold_id"] == hold["hold_id"]
    assert inventory.get_hold(hold["hold_id"])["expiry_message"]["message_id"]


def test_expiry_queue_exists_with_dlq():
    attrs = sqs().get_queue_attributes(QueueUrl=queue_url(settings().expiry_queue), AttributeNames=["All"])["Attributes"]
    assert json.loads(attrs["RedrivePolicy"])["maxReceiveCount"] in (5, "5")
