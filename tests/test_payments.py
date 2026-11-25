"""Payment events through the real queue and Lambda: release, confirm, duplicates, poison messages, DLQ."""
import json
import time

from stockhold import faults, inventory, payments
from stockhold.states import CONFIRMED, HELD, RELEASED

from .test_expiry import wait_status


def find_in_dlq(message_ids: set, timeout=60) -> dict:
    """Wait until every message has been dead-lettered and archived by the replay tool's sync.

    (Archiving moves it out of the SQS DLQ. A running demo server also syncs in the background,
    so this must not assume it is the only reader of the DLQ.)
    """
    from stockhold import dlq
    found, t0 = {}, time.time()
    while set(found) != message_ids and time.time() - t0 < timeout:
        dlq.sync()
        for mid in message_ids - set(found):
            rec = dlq._find(mid)
            if rec:
                found[mid] = rec
        time.sleep(1)
    missing = message_ids - set(found)
    assert not missing, f"not dead-lettered after {timeout}s: {missing}"
    return found


def test_payment_failed_releases_through_lambda(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 2, "o-1")
    payments.publish("payment_failed", h["hold_id"])
    got, _ = wait_status(h["hold_id"], RELEASED, timeout=30)
    assert got["history"][-1]["by"] == "payment-events"
    assert inventory.stock_report(sku)["available"] == 3


def test_payment_succeeded_confirms(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 1, "o-1")
    payments.publish("payment_succeeded", h["hold_id"])
    wait_status(h["hold_id"], CONFIRMED, timeout=30)
    assert inventory.stock_report(sku)["confirmed"] == 1


def test_duplicate_messages_on_the_queue_release_once(new_sku):
    sku = new_sku(4)
    h = inventory.reserve(sku, 3, "o-1")
    for _ in range(4):                       # the provider retried its webhook four times
        payments.publish("payment_failed", h["hold_id"], event_id="evt-same")
    wait_status(h["hold_id"], RELEASED, timeout=30)
    time.sleep(3)                            # let the other copies run too
    assert inventory.stock_report(sku)["available"] == 4
    assert [e["to"] for e in inventory.get_hold(h["hold_id"])["history"]].count(RELEASED) == 1


# @learn:test-poison-dlq
def test_bad_messages_are_retried_then_dead_lettered_with_a_reason(new_sku):
    sku = new_sku(4)
    flaky = inventory.reserve(sku, 1, "o-flaky")
    faults.arm(flaky["hold_id"], 3)                       # order service down for exactly three attempts
    late = inventory.reserve(sku, 1, "o-late", hold_seconds=1)
    time.sleep(1.1)                                       # paid after the hold expired
    sent = {
        "MALFORMED_EVENT": payments.publish_raw("{not json")["message_id"],
        "HOLD_NOT_FOUND": payments.publish("payment_failed", "h_no_such_hold")["message_id"],
        "DOWNSTREAM_UNAVAILABLE": payments.publish("payment_failed", flaky["hold_id"])["message_id"],
        "HOLD_EXPIRED": payments.publish("payment_succeeded", late["hold_id"])["message_id"],
    }
    dlq = find_in_dlq(set(sent.values()))
    for code, mid in sent.items():
        f = payments.failures(mid)
        assert f["last_code"] == code, (code, f)
        assert [a["receive_count"] for a in f["attempts"]] == [1, 2, 3]      # maxReceiveCount = 3
        assert dlq[mid]["receive_count"] >= 3 and dlq[mid]["code"] == code
    assert inventory.get_hold(flaky["hold_id"])["status"] == HELD          # nothing half-applied
    assert faults.get(flaky["hold_id"])["remaining"] == 0
# @learn:end test-poison-dlq


def test_parse_rejects_bad_shapes():
    import pytest
    for raw in ("[]", json.dumps({"type": "refund", "hold_id": "h"}), json.dumps({"type": "payment_failed"})):
        with pytest.raises(payments.MalformedEvent):
            payments.parse(raw)


def test_fault_counts_down_and_clears():
    target = f"h_fault_{time.time_ns()}"
    faults.arm(target, 2)
    assert [faults.fire(target) for _ in range(3)] == [True, True, False]
    assert faults.fire("h_other_" + target) is False
    faults.clear(target)
    assert faults.get(target) is None
