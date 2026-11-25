"""Lambda: payment-events queue -> confirm/release. A failing message is retried, then dead-lettered."""
import json
import time
from concurrent.futures import ThreadPoolExecutor

from stockhold import payments, tracing


def _one(rec):
    n = int(rec.get("attributes", {}).get("ApproximateReceiveCount", "1"))
    parent = rec.get("messageAttributes", {}).get("traceparent", {}).get("stringValue")
    try:
        with tracing.span("payments.handle", parent=parent, message_id=rec["messageId"], receive_count=n) as sp:
            r = payments.handle(rec["body"])
            sp.update(hold_id=r["hold_id"], status=r["status"], changed=r["changed"])
        print(json.dumps({"fn": "payments", "message_id": rec["messageId"], "receive_count": n, **r}))
        return None
    except Exception as e:  # noqa: BLE001 - this message only; SQS redelivers it after the visibility timeout
        payments.record_failure(rec["messageId"], rec["body"], e, n, int(time.time() * 1000))
        print(json.dumps({"fn": "payments", "message_id": rec["messageId"], "receive_count": n,
                          "error": getattr(e, "code", e.__class__.__name__), "detail": str(e)[:200]}))
        return {"itemIdentifier": rec["messageId"]}


# @learn:payments-batch
def handler(event, context):
    # The records of a batch are independent (standard queue, no ordering), so they run in parallel:
    # handled one after another, a burst of events queued up for ten seconds and paid holds expired.
    recs = event.get("Records", [])
    with ThreadPoolExecutor(max_workers=max(1, min(16, len(recs)))) as pool:
        failures = [f for f in pool.map(_one, recs) if f]
    return {"batchItemFailures": failures}
# @learn:end payments-batch
