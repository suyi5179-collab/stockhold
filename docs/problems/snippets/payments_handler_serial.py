"""Before the fix (kept for docs/problems.md q04): the records of a batch, one after another.

Lambda: payment-events queue -> confirm/release. A failing message is retried, then dead-lettered."""
import json
import time

from stockhold import payments


# @learn:q-payments-serial
def handler(event, context):
    failures = []
    for rec in event.get("Records", []):
        n = int(rec.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            r = payments.handle(rec["body"])
            print(json.dumps({"fn": "payments", "message_id": rec["messageId"], "receive_count": n, **r}))
        except Exception as e:  # noqa: BLE001 - this message only; SQS redelivers it after the visibility timeout
            payments.record_failure(rec["messageId"], rec["body"], e, n, int(time.time() * 1000))
            print(json.dumps({"fn": "payments", "message_id": rec["messageId"], "receive_count": n,
                              "error": getattr(e, "code", e.__class__.__name__), "detail": str(e)[:200]}))
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
# @learn:end q-payments-serial
