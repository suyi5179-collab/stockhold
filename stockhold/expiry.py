"""Eventual release: a delayed SQS message per hold, and a sweeper that catches whatever it missed.

No lock is held while a customer pays. A hold that runs out keeps its units until the expiry
message (or, if that was lost, the sweeper) moves it to EXPIRED. docs/design.md measures that window.
"""
from __future__ import annotations

import json
import logging

from botocore.exceptions import ClientError

from . import inventory, keys, tracing
from .aws import ddb, queue_url, sqs
from .config import settings
from .dynamo import item, plain, vals
from .states import HELD, HoldNotFound, InvalidTransition

log = logging.getLogger("stockhold.expiry")
MAX_DELAY = 900  # SQS DelaySeconds ceiling; longer holds are re-delayed in hops


def schedule(hold: dict) -> dict:
    """Send the hold's expiry message, delayed until expiresAt (or the 15-minute SQS ceiling)."""
    # @learn:schedule-expiry
    remaining = max(0, (hold["expires_at_ms"] - inventory.now_ms() + 999) // 1000)
    body = {"type": "hold.expire", "hold_id": hold["hold_id"], "sku": hold["sku"], "expires_at_ms": hold["expires_at_ms"]}
    delay = int(min(remaining, MAX_DELAY))
    tp = tracing.traceparent()
    r = sqs().send_message(QueueUrl=queue_url(settings().expiry_queue), MessageBody=json.dumps(body), DelaySeconds=delay,
                           **({"MessageAttributes": {"traceparent": {"DataType": "String", "StringValue": tp}}} if tp else {}))
    # @learn:end schedule-expiry
    msg = {"message_id": r["MessageId"], "delay_seconds": delay, "body": body, "queue": settings().expiry_queue}
    try:
        ddb().update_item(TableName=settings().table, Key=item({"PK": keys.hold_pk(hold["hold_id"]), "SK": "HOLD"}),
                          UpdateExpression="SET expiry_message = :m", ConditionExpression="attribute_exists(PK)",
                          ExpressionAttributeValues=vals({":m": msg}))
    except ClientError:
        log.warning("could not record expiry message on %s", hold["hold_id"])
    return msg


# @learn:expire-handler
def handle_message(body: dict) -> str:
    """Process one expiry message. Returns what happened; raising makes SQS redeliver it."""
    try:
        hold = inventory.get_hold(body["hold_id"])
    except HoldNotFound:
        return "missing"                      # the reserve that sent it never committed
    if hold["status"] != HELD:
        return "already-" + hold["status"].lower()   # paid, released, or a duplicate of this message
    left_ms = hold["expires_at_ms"] - inventory.now_ms()
    if left_ms > 1000:                        # long hold: delayed in 15-minute hops
        schedule(hold)
        return "rescheduled"
    try:
        r = inventory.expire(hold["hold_id"], by="expiry-message")
    except InvalidTransition:
        return "lost-race"                   # confirmed or released between our read and our write
    return "expired" if r["changed"] else "already-expired"
# @learn:end expire-handler


def overdue(now_ms: int | None = None, limit: int = 100) -> list[dict]:
    """Open holds past expiresAt (minus a grace period), read from the sparse GSI1, every bucket."""
    cutoff = (now_ms or inventory.now_ms()) - settings().sweep_grace_ms
    out = []
    for b in range(keys.HELD_BUCKETS):
        r = ddb().query(TableName=settings().table, IndexName=keys.GSI1, Limit=limit,
                        KeyConditionExpression="gsi1pk = :p AND gsi1sk < :c",
                        ExpressionAttributeValues=vals({":p": f"HELD#{b}", ":c": cutoff}))
        out += [plain(i) for i in r["Items"]]
    return out


# @learn:sweeper
def sweep(now_ms: int | None = None) -> dict:
    """Backstop for lost or failed expiry messages. Safe to run concurrently with them."""
    expired = skipped = 0
    for h in overdue(now_ms):
        try:
            r = inventory.expire(h["hold_id"], by="sweeper")
            expired += r["changed"]
            skipped += not r["changed"]
        except (InvalidTransition, HoldNotFound):
            skipped += 1
    return {"expired": expired, "skipped": skipped}
# @learn:end sweeper
