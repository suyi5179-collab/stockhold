"""Lambda: the hold-expiry queue (delayed messages) -> expire the hold if it is still HELD."""
import json

from stockhold import expiry


def handler(event, context):
    failures = []
    for rec in event.get("Records", []):
        try:
            outcome = expiry.handle_message(json.loads(rec["body"]))
            print(json.dumps({"fn": "expire", "message_id": rec["messageId"],
                              "hold_id": json.loads(rec["body"]).get("hold_id"), "outcome": outcome}))
        except Exception as e:  # noqa: BLE001  - report this one message, keep the rest of the batch
            print(json.dumps({"fn": "expire", "message_id": rec["messageId"], "error": repr(e)}))
            failures.append({"itemIdentifier": rec["messageId"]})
    return {"batchItemFailures": failures}
