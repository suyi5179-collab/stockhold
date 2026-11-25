"""Lambda: the hold-expiry queue (delayed messages) -> expire the hold if it is still HELD."""
import json
from concurrent.futures import ThreadPoolExecutor

from stockhold import expiry, tracing


def _one(rec):
    parent = rec.get("messageAttributes", {}).get("traceparent", {}).get("stringValue")
    try:
        body = json.loads(rec["body"])
        with tracing.span("expire.handle", parent=parent, hold_id=body.get("hold_id"), message_id=rec["messageId"]) as sp:
            outcome = expiry.handle_message(body)
            sp["outcome"] = outcome
        print(json.dumps({"fn": "expire", "message_id": rec["messageId"], "hold_id": body.get("hold_id"), "outcome": outcome}))
        return None
    except Exception as e:  # noqa: BLE001  - report this one message, keep the rest of the batch
        print(json.dumps({"fn": "expire", "message_id": rec["messageId"], "error": repr(e)}))
        return {"itemIdentifier": rec["messageId"]}


def handler(event, context):
    recs = event.get("Records", [])
    with ThreadPoolExecutor(max_workers=max(1, min(10, len(recs)))) as pool:
        return {"batchItemFailures": [f for f in pool.map(_one, recs) if f]}
