"""Payment events -> confirm or release. Failures are retried by SQS, then dead-lettered.

Every failed attempt is recorded (FAIL#<message_id>) with a stable error code, so the replay tool
can say *why* a message is in the DLQ without re-running it.
"""
from __future__ import annotations

import json
import uuid

from . import faults, inventory, tracing
from .aws import ddb, queue_url, sqs
from .config import settings
from .dynamo import item, plain, vals
from .states import HoldExpired, HoldNotFound, InvalidTransition, StockholdError

TYPES = {"payment_failed": "release", "payment_succeeded": "confirm"}


class MalformedEvent(StockholdError):
    code, status = "MALFORMED_EVENT", 400


class DownstreamUnavailable(StockholdError):
    code, status = "DOWNSTREAM_UNAVAILABLE", 503


def publish(event_type: str, hold_id: str, event_id: str | None = None, extra: dict | None = None,
            attributes: dict | None = None) -> dict:
    body = {"type": event_type, "hold_id": hold_id, "event_id": event_id or "evt_" + uuid.uuid4().hex[:16], **(extra or {})}
    return publish_raw(json.dumps(body), attributes)


def publish_raw(raw: str, attributes: dict | None = None) -> dict:
    attrs = {k: {"DataType": "String", "StringValue": str(v)} for k, v in (attributes or {}).items()}
    tp = tracing.traceparent()
    if tp:   # the payments Lambda continues this trace (API -> queue -> Lambda in one Jaeger trace)
        attrs["traceparent"] = {"DataType": "String", "StringValue": tp}
    r = sqs().send_message(QueueUrl=queue_url(settings().payments_queue), MessageBody=raw,
                           **({"MessageAttributes": attrs} if attrs else {}))
    return {"message_id": r["MessageId"], "body": raw}


def parse(raw: str) -> dict:
    try:
        body = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise MalformedEvent("body is not JSON") from e
    if not isinstance(body, dict):
        raise MalformedEvent("body is not a JSON object")
    if body.get("type") not in TYPES:
        raise MalformedEvent(f"unknown event type {body.get('type')!r}")
    if not isinstance(body.get("hold_id"), str) or not body["hold_id"]:
        raise MalformedEvent("hold_id missing")
    return body


# @learn:payment-handler
def handle(raw: str) -> dict:
    """Apply one payment event. Raises StockholdError subclasses; the caller decides retry vs. done."""
    body = parse(raw)
    if faults.fire(body["hold_id"]):
        raise DownstreamUnavailable("injected fault: the order service is down", hold_id=body["hold_id"])
    if body["type"] == "payment_failed":
        r = inventory.release(body["hold_id"], by="payment-events", reason="payment failed")
    else:
        r = inventory.confirm(body["hold_id"], by="payment-events")
    return {"hold_id": body["hold_id"], "status": r["hold"]["status"], "changed": r["changed"]}
# @learn:end payment-handler


def record_failure(message_id: str, raw: str, err: Exception, receive_count: int, at_ms: int) -> None:
    """Append this attempt to FAIL#<message_id>; the replay tool joins it with the DLQ copy."""
    code = getattr(err, "code", err.__class__.__name__)
    try:
        hold_id = json.loads(raw).get("hold_id")
    except Exception:  # noqa: BLE001
        hold_id = None
    attempt = {"receive_count": receive_count, "code": code, "message": str(err)[:300], "at_ms": at_ms}
    ddb().update_item(
        TableName=settings().table, Key=item({"PK": f"FAIL#{message_id}", "SK": "FAIL"}),
        UpdateExpression="SET attempts = list_append(if_not_exists(attempts, :empty), :a), last_code = :c, "
                         "hold_id = :h, message_id = :m, first_at_ms = if_not_exists(first_at_ms, :t), last_at_ms = :t",
        ExpressionAttributeValues=vals({":empty": [], ":a": [attempt], ":c": code, ":h": hold_id or "",
                                        ":m": message_id, ":t": at_ms}))


def failures(message_id: str) -> dict | None:
    return plain(ddb().get_item(TableName=settings().table, ConsistentRead=True,
                                Key=item({"PK": f"FAIL#{message_id}", "SK": "FAIL"})).get("Item"))


# Which errors are worth retrying. Every error is retried by SQS (maxReceiveCount) because the
# consumer cannot always tell, but these codes say "a human must look" once it lands in the DLQ.
PERMANENT = {MalformedEvent.code, HoldNotFound.code, InvalidTransition.code, HoldExpired.code}
