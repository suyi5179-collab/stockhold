"""Lambda: DynamoDB Streams on the table -> hold state changes as JSON Lines in S3."""
import json

from stockhold import eventlog


def handler(event, context):
    evs = [e for e in (eventlog.to_event(r) for r in event.get("Records", [])) if e]
    keys = eventlog.write(evs, getattr(context, "aws_request_id", "local")) if evs else []
    print(json.dumps({"fn": "eventlog", "records": len(event.get("Records", [])), "events": len(evs),
                      "holds": sorted({e["hold_id"] for e in evs})[:20], "keys": keys}))
    return {"events": len(evs)}
