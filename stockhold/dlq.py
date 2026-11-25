"""The DLQ replay tool's backend: archive, search, compare, dry-run, replay, audit, alerts.

Browsing an SQS queue in place is not possible without receiving (which bumps receive counts and
hides messages), so the tool first *archives* each dead-lettered message into DynamoDB
(DLQMSG, joined with its failure record) and deletes it from the DLQ. The archive is what on-call
searches; replaying sends the original body back to the payment-events queue.
"""
from __future__ import annotations

import json
import uuid

from botocore.exceptions import ClientError

from . import faults, inventory, payments
from .aws import ddb, queue_url, sqs
from .config import settings
from .dynamo import error_code, item, plain, vals
from .states import HELD, HoldNotFound, StockholdError

DLQ_PK = "DLQ"            # one item collection, newest last; see docs/design.md for when that stops scaling
AUDIT_PK = "AUDIT"


class ReplayBlocked(StockholdError):
    code, status = "REPLAY_BLOCKED", 409


class AlreadyReplayed(StockholdError):
    code, status = "ALREADY_REPLAYED", 409


class MessageNotFound(StockholdError):
    code, status = "MESSAGE_NOT_FOUND", 404


def _sk(archived_at_ms: int, message_id: str) -> str:
    return f"{archived_at_ms:013d}#{message_id}"


# @learn:dlq-archive
def sync(max_batches: int = 20) -> int:
    """Move everything currently in the DLQ into the archive. Returns how many were archived."""
    url = queue_url(settings().payments_dlq)
    n = 0
    for _ in range(max_batches):
        r = sqs().receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=0, VisibilityTimeout=30,
                                  AttributeNames=["All"], MessageAttributeNames=["All"])
        msgs = r.get("Messages", [])
        if not msgs:
            break
        for m in msgs:
            _archive(m)
            # Delete only after the archive write succeeded: a crash in between re-archives (a no-op).
            sqs().delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
            n += 1
    return n
# @learn:end dlq-archive


def _archive(m: dict) -> None:
    mid = m["MessageId"]
    fail = payments.failures(mid) or {}
    try:
        body = json.loads(m["Body"])
        hold_id = body.get("hold_id") if isinstance(body, dict) else None
    except ValueError:
        body, hold_id = None, None
    sku = (body or {}).get("sku") if isinstance(body, dict) else None
    if hold_id and not sku:
        try:
            sku = inventory.get_hold(hold_id)["sku"]
        except HoldNotFound:
            pass
    now = inventory.now_ms()
    rec = {
        "PK": DLQ_PK, "SK": _sk(now, mid), "message_id": mid, "raw": m["Body"], "hold_id": hold_id or "",
        "sku": sku or "", "event_type": (body or {}).get("type", "") if isinstance(body, dict) else "",
        "code": fail.get("last_code", "UNKNOWN"), "attempts": fail.get("attempts", []),
        # @learn:dlq-receive-count
        # SQS counts our own archiving receive too; subtract it so this is "times the consumer saw it".
        "receive_count": max(0, int(m["Attributes"].get("ApproximateReceiveCount", "1")) - 1),
        # @learn:end dlq-receive-count
        "sent_at_ms": int(m["Attributes"].get("SentTimestamp", "0")), "first_failed_at_ms": fail.get("first_at_ms", now),
        "archived_at_ms": now, "status": "OPEN",
        "message_attributes": {k: v.get("StringValue") for k, v in m.get("MessageAttributes", {}).items()},
    }
    # Idempotent: the same message archived twice (crash before delete) keeps the first copy.
    if _find(mid):
        return
    ddb().put_item(TableName=settings().table, Item=item(rec))
    ddb().put_item(TableName=settings().table, Item=item({"PK": f"DLQIDX#{mid}", "SK": "IDX", "sk": rec["SK"]}))


def _find(mid: str) -> dict | None:
    idx = plain(ddb().get_item(TableName=settings().table, Key=item({"PK": f"DLQIDX#{mid}", "SK": "IDX"}),
                               ConsistentRead=True).get("Item"))
    if not idx:
        return None
    return plain(ddb().get_item(TableName=settings().table, Key=item({"PK": DLQ_PK, "SK": idx["sk"]}),
                                ConsistentRead=True).get("Item"))


def _all() -> list[dict]:
    out, kw = [], {}
    while True:
        r = ddb().query(TableName=settings().table, KeyConditionExpression="PK = :p", ScanIndexForward=False,
                        ExpressionAttributeValues=vals({":p": DLQ_PK}), ConsistentRead=True, **kw)
        out += [plain(i) for i in r["Items"]]
        if "LastEvaluatedKey" not in r:
            return out
        kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}


def _brief(r: dict) -> dict:
    return {k: r.get(k) for k in ("message_id", "hold_id", "sku", "event_type", "code", "receive_count",
                                  "sent_at_ms", "archived_at_ms", "status", "replay")}


# @learn:dlq-search
def search(q: str = "", code: str = "", since_ms: int = 0, until_ms: int = 0, status: str = "") -> list[dict]:
    """Free text matches hold id, sku, message id or the raw body; the rest are exact filters."""
    q = q.strip().lower()
    out = []
    for r in _all():
        if q and not any(q in str(r.get(f, "")).lower() for f in ("hold_id", "sku", "message_id", "raw")):
            continue
        if code and r["code"] != code:
            continue
        if since_ms and r["archived_at_ms"] < since_ms:
            continue
        if until_ms and r["archived_at_ms"] > until_ms:
            continue
        if status and r["status"] != status:
            continue
        out.append(_brief(r))
    return out
# @learn:end dlq-search


def diff_json(a, b, path: str = "") -> list[dict]:
    """Flat field-by-field comparison of two JSON values."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += diff_json(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [{"path": path or "$", "a": a, "b": b, "same": a == b}]


def detail(message_id: str) -> dict:
    r = _find(message_id)
    if not r:
        raise MessageNotFound(f"message {message_id} is not in the archive")
    try:
        body = json.loads(r["raw"])
    except ValueError:
        body = None
    hold = None
    if r["hold_id"]:
        try:
            hold = inventory.get_hold(r["hold_id"])
        except HoldNotFound:
            pass
    # Message vs. the hold as it is now: the fields both talk about, plus what the event expects.
    cmp = []
    if isinstance(body, dict):
        for f in ("hold_id", "order_id", "sku", "qty"):
            if f in body:
                cmp.append({"path": f, "a": body.get(f), "b": hold.get(f) if hold else None,
                            "same": bool(hold) and body.get(f) == hold.get(f)})
        cmp.append({"path": "status", "a": "HELD (expected)", "b": hold["status"] if hold else None,
                    "same": bool(hold) and hold["status"] == HELD})
    return {"message": {k: v for k, v in r.items() if k not in ("PK", "SK")}, "body": body, "hold": hold,
            "compare": cmp, "plan": plan(r, body, hold)}


def compare(a_id: str, b_id: str) -> list[dict]:
    a, b = _find(a_id), _find(b_id)
    if not a or not b:
        raise MessageNotFound("both messages must be in the archive")
    load = lambda r: json.loads(r["raw"]) if r["raw"].strip().startswith("{") else {"raw": r["raw"]}  # noqa: E731
    return diff_json({"body": load(a), "code": a["code"], "receive_count": a["receive_count"]},
                     {"body": load(b), "code": b["code"], "receive_count": b["receive_count"]})


# @learn:dry-run
def plan(rec: dict, body, hold) -> dict:
    """What a replay would do right now, or why it must not happen. Pure: reads, never writes."""
    if rec["status"] == "REPLAYED":
        return {"allowed": False, "reason": "already replayed", "replay": rec.get("replay")}
    if not isinstance(body, dict) or body.get("type") not in payments.TYPES:
        return {"allowed": False, "reason": "malformed payload: fix the producer; replaying cannot help"}
    if hold is None:
        return {"allowed": False, "reason": f"hold {body.get('hold_id')} does not exist"}
    if hold["status"] != HELD:
        return {"allowed": False, "reason": f"hold is {hold['status']}: a replay would change nothing"
                + (" (paid after expiry: refund the customer instead)" if hold["status"] == "EXPIRED" and body["type"] == "payment_succeeded" else "")}
    if body["type"] == "payment_succeeded" and hold["expires_at_ms"] <= inventory.now_ms():
        return {"allowed": False, "reason": "hold expired: refund the customer instead of confirming"}
    shard = next(s for s in inventory.read_shards(hold["sku"]) if s["shard"] == hold["shard"])
    armed = faults.get(hold["hold_id"])
    effect = ({"transition": "HELD -> RELEASED", "shard": hold["shard"], "available_before": shard["available"],
               "available_after": shard["available"] + hold["qty"]}
              if body["type"] == "payment_failed" else {"transition": "HELD -> CONFIRMED", "shard": hold["shard"]})
    return {"allowed": True, "effect": effect,
            "warning": f"fault still armed ({armed['remaining']} left): the replay will fail again"
            if armed and armed.get("remaining", 0) > 0 else None}
# @learn:end dry-run


def dry_run(message_id: str) -> dict:
    return detail(message_id)["plan"]


# @learn:replay
def replay(message_id: str, actor: str) -> dict:
    """Send the original body back to the payment-events queue, at most once per archived message."""
    d = detail(message_id)
    rec, p = d["message"], d["plan"]
    if not p["allowed"]:
        audit(actor, "replay-refused", rec, p)
        if rec["status"] == "REPLAYED":
            raise AlreadyReplayed("this message was already replayed", replay=rec.get("replay"))
        raise ReplayBlocked(p["reason"], plan=p)
    replay_id = "rp_" + uuid.uuid4().hex[:12]
    sk = _find(message_id)["SK"]
    try:
        # The claim: only one click, from one engineer, gets to send it.
        ddb().update_item(TableName=settings().table, Key=item({"PK": DLQ_PK, "SK": sk}),
                          UpdateExpression="SET #s = :replaying, replay = :r",
                          ConditionExpression="#s = :open", ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues=vals({":replaying": "REPLAYING", ":open": "OPEN",
                                                          ":r": {"replay_id": replay_id, "by": actor,
                                                                 "at_ms": inventory.now_ms()}}))
    except ClientError as e:
        if error_code(e) != "ConditionalCheckFailedException":
            raise
        audit(actor, "replay-refused", rec, {"allowed": False, "reason": "already replayed"})
        raise AlreadyReplayed("this message was already replayed") from e
    sent = payments.publish_raw(rec["raw"], {"replay_of": message_id, "replay_id": replay_id})
    info = {"replay_id": replay_id, "by": actor, "at_ms": inventory.now_ms(), "new_message_id": sent["message_id"]}
    ddb().update_item(TableName=settings().table, Key=item({"PK": DLQ_PK, "SK": sk}),
                      UpdateExpression="SET #s = :done, replay = :r", ExpressionAttributeNames={"#s": "status"},
                      ExpressionAttributeValues=vals({":done": "REPLAYED", ":r": info}))
    audit(actor, "replay", rec, p, info)
    return {"replayed": True, **info, "plan": p}
# @learn:end replay


def audit(actor: str, action: str, rec: dict, plan_: dict, result: dict | None = None) -> dict:
    now = inventory.now_ms()
    a = {"PK": AUDIT_PK, "SK": f"{now:013d}#{uuid.uuid4().hex[:6]}", "actor": actor, "action": action,
         "message_id": rec["message_id"], "hold_id": rec.get("hold_id", ""), "plan": plan_, "result": result,
         "at_ms": now}
    ddb().put_item(TableName=settings().table, Item=item(a))
    return a


def audit_log(limit: int = 100, message_id: str = "") -> list[dict]:
    r = ddb().query(TableName=settings().table, KeyConditionExpression="PK = :p", ScanIndexForward=False,
                    Limit=limit if not message_id else 1000, ExpressionAttributeValues=vals({":p": AUDIT_PK}))
    rows = [{k: v for k, v in plain(i).items() if k not in ("PK", "SK")} for i in r["Items"]]
    return [x for x in rows if not message_id or x["message_id"] == message_id][:limit]


# @learn:alerts
QUEUE_AGE_LIMIT_S = 300       # oldest unhandled dead letter older than five minutes
REDRIVE_WARN, REDRIVE_FIRE = 1, 5   # messages dead-lettered in the last fifteen minutes


def alerts(now_ms: int | None = None) -> dict:
    now = now_ms or inventory.now_ms()
    rows = _all()
    open_rows = [r for r in rows if r["status"] == "OPEN"]
    oldest = max((now - r["first_failed_at_ms"]) / 1000 for r in open_rows) if open_rows else 0.0
    recent = sum(1 for r in rows if now - r["archived_at_ms"] <= 15 * 60 * 1000)
    q = sqs().get_queue_attributes(QueueUrl=queue_url(settings().payments_queue),
                                   AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return {
        "open": len(open_rows), "oldest_open_age_s": round(oldest, 1), "redriven_15m": recent,
        "payments_queue_visible": int(q["ApproximateNumberOfMessages"]),
        "payments_queue_in_flight": int(q["ApproximateNumberOfMessagesNotVisible"]),
        "alerts": [
            {"name": "DLQOldestMessageAge", "state": "firing" if oldest > QUEUE_AGE_LIMIT_S else "ok",
             "value": round(oldest, 1), "threshold": QUEUE_AGE_LIMIT_S},
            {"name": "DLQRedriveVolume", "state": "firing" if recent >= REDRIVE_FIRE else "warning" if recent >= REDRIVE_WARN else "ok",
             "value": recent, "threshold": REDRIVE_FIRE},
        ],
    }
# @learn:end alerts
