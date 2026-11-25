"""Teaching mode: run one sample order and report, from the real stores, how far it got.

Nothing here is animated or simulated. Every stage's status is derived from DynamoDB (the hold,
its history, failure records, the DLQ archive, the audit log), SQS message ids the code recorded,
CloudWatch Logs written by the Lambdas, the S3 event log and a DuckDB query over it.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from . import analytics, dlq, eventlog, expiry, faults, inventory, payments, tracing
from .aws import client, ddb
from .config import settings
from .dynamo import item, plain
from .states import HoldNotFound, ValidationError

LEARN_SKU = "learn-tee"
SCENARIOS = {
    "payment_failed": {"hold_seconds": 120, "title": "付款失败 → 释放"},
    "timeout": {"hold_seconds": 15, "title": "没人付款 → 到期释放"},
    "dlq": {"hold_seconds": 600, "title": "坏消息 → 重试 → 死信 → 重放"},
}

# The stages, in order. `src` lists the @learn anchors shown as "看源码"; `part` is the video part.
MAIN = [
    {"id": "order", "title": "提交样例订单", "part": "p01-reserve", "src": ["api-schedule"]},
    {"id": "reserve", "title": "reserve：条件写扣库存", "part": "p01-reserve", "src": ["reserve-transact", "idempotent-replay"]},
    {"id": "enqueue", "title": "延迟消息入队", "part": "p03-expiry", "src": ["schedule-expiry", "cdk-expiry"]},
    {"id": "trigger", "title": "付款失败事件 / 到期", "part": "p04-payments-dlq", "src": ["payment-handler", "expire-handler"]},
    {"id": "lambda", "title": "release / expire Lambda", "part": "p02-idempotency", "src": ["transition-guard", "release-transact"]},
    {"id": "s3", "title": "事件写进 S3", "part": "p07-eventlog", "src": ["stream-to-event", "cdk-eventlog"]},
    {"id": "duckdb", "title": "DuckDB 查询结果", "part": "p07-eventlog", "src": ["sql-rates", "duckdb-view"]},
]
BRANCH = [
    {"id": "bad", "title": "坏消息：下游故障", "part": "p04-payments-dlq", "src": ["payment-handler"]},
    {"id": "retry", "title": "重试三次", "part": "p04-payments-dlq", "src": ["cdk-payments"]},
    {"id": "dlq", "title": "进 DLQ 并归档", "part": "p05-replay", "src": ["dlq-archive"]},
    {"id": "dryrun", "title": "重放工具里 dry-run", "part": "p05-replay", "src": ["dry-run", "dlq-search"]},
    {"id": "replay", "title": "安全重放", "part": "p05-replay", "src": ["replay", "replay-endpoint"]},
    {"id": "released", "title": "重放成功：库存回来了", "part": "p05-replay", "src": ["release-transact"]},
]


def _key(run_id: str) -> dict:
    return item({"PK": f"LEARN#{run_id}", "SK": "RUN"})


def ensure_sku() -> None:
    try:
        inventory.create_sku(LEARN_SKU, 100000, 4, "教学用 T 恤", 1900)
    except ValidationError:
        pass


# @learn:learn-start
def start(scenario: str, actor: str = "learn") -> dict:
    if scenario not in SCENARIOS:
        raise ValidationError(f"scenario must be one of {sorted(SCENARIOS)}")
    ensure_sku()
    run_id = "lr_" + uuid.uuid4().hex[:10]
    req = {"sku": LEARN_SKU, "qty": 2, "order_id": f"learn-{run_id}", "hold_seconds": SCENARIOS[scenario]["hold_seconds"]}
    with tracing.span("learn.submit", scenario=scenario, run_id=run_id) as sp:
        hold = inventory.reserve(req["sku"], req["qty"], req["order_id"], req["hold_seconds"],
                                 client=actor, idempotency_key=run_id)
        hold["expiry_message"] = expiry.schedule(hold)
        sp["hold_id"] = hold["hold_id"]
        trace_id, tp = tracing.current_trace_id(), tracing.traceparent()
    run = {"PK": f"LEARN#{run_id}", "SK": "RUN", "run_id": run_id, "scenario": scenario, "hold_id": hold["hold_id"],
           "sku": LEARN_SKU, "started_at_ms": inventory.now_ms(), "trace_id": trace_id or "",
           "request": {"method": "POST", "path": "/v1/reservations", "headers": {"Idempotency-Key": run_id}, "body": req},
           "response": {"status": 201, "hold_id": hold["hold_id"], "status_field": hold["status"]}}
    ddb().put_item(TableName=settings().table, Item=item(run))
    if scenario == "dlq":
        faults.arm(hold["hold_id"], 3)
        _send_payment(run_id, hold, tp)
    elif scenario == "payment_failed":
        # The provider's webhook arrives a few seconds later, like a real declined card.
        threading.Timer(4.0, _send_payment, args=(run_id, hold, tp)).start()
    return {"run_id": run_id, "hold_id": hold["hold_id"], "scenario": scenario}
# @learn:end learn-start


def _send_payment(run_id: str, hold: dict, parent: str | None = None) -> None:
    # Same trace as the order: the webhook span, the queue hop and the payments Lambda line up in Jaeger.
    with tracing.span("payment.webhook", parent=parent, hold_id=hold["hold_id"]):
        sent = payments.publish("payment_failed", hold["hold_id"], extra={"order_id": hold["order_id"], "sku": hold["sku"],
                                                                          "qty": hold["qty"]})
    ddb().update_item(TableName=settings().table, Key=_key(run_id), UpdateExpression="SET payment_message = :m",
                      ExpressionAttributeValues={":m": item({"m": {**sent, "body": json.loads(sent["body"])}})["m"]})


def get_run(run_id: str) -> dict:
    r = plain(ddb().get_item(TableName=settings().table, Key=_key(run_id), ConsistentRead=True).get("Item"))
    if not r:
        raise HoldNotFound(f"learn run {run_id} not found")
    return r


def _lambda_log(fn: str, needle: str) -> list[str]:
    logs = client("logs")
    group = f"/aws/lambda/stockhold-{fn}"
    try:
        streams = logs.describe_log_streams(logGroupName=group, orderBy="LastEventTime", descending=True, limit=5)["logStreams"]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for st in streams:
        for e in logs.get_log_events(logGroupName=group, logStreamName=st["logStreamName"], limit=300)["events"]:
            if needle in e["message"]:
                out.append(e["message"].strip())
    return out[-4:]


_duck_cache: dict = {}


def _duckdb(sku: str, hold_id: str) -> dict | None:
    """Mirror the event log and run the rates query for this product (cached for a few seconds)."""
    now = time.time()
    if _duck_cache.get("at", 0) > now - 5 and _duck_cache.get("hold") == hold_id:
        return _duck_cache["res"]
    d = ".runtime/eventlog"
    eventlog.download(d)
    rates = analytics.run(d, "rates_by_product", where_sku=sku)
    rec = analytics.run(d, "hold_timeout_recommendation", where_sku=sku)
    res = {"rates": rates, "recommendation": rec}
    _duck_cache.update(at=now, hold=hold_id, res=res)
    return res


def _hist(hold: dict, to: str) -> dict | None:
    return next((h for h in hold["history"] if h["to"] == to), None)


# @learn:learn-trace
def trace(run_id: str) -> dict:
    run = get_run(run_id)
    hold = inventory.get_hold(run["hold_id"])
    shard = next(s for s in inventory.read_shards(hold["sku"]) if s["shard"] == hold["shard"])
    st: dict[str, dict] = {}
    st["order"] = {"done": True, "data": {"request": run["request"], "response": run["response"]}}
    st["reserve"] = {"done": True, "data": {"reserve": hold["reserve"], "shard_now": shard,
                                            "condition": "available >= :n AND version = :v",
                                            "hold_row": {k: hold[k] for k in ("hold_id", "status", "version", "qty", "shard", "expires_at_ms")}}}
    em = hold.get("expiry_message")
    st["enqueue"] = {"done": bool(em), "data": em}
    terminal = hold["status"] != "HELD"
    if run["scenario"] == "timeout":
        left = hold["expires_at_ms"] - inventory.now_ms()
        st["trigger"] = {"done": left <= 0, "data": {"kind": "timeout", "expires_at_ms": hold["expires_at_ms"],
                                                     "seconds_left": max(0, round(left / 1000, 1))}}
    else:
        pm = run.get("payment_message")
        st["trigger"] = {"done": bool(pm), "data": pm and {"kind": "payment_failed", **pm}}
    final = next((h for h in hold["history"] if h["to"] in ("RELEASED", "EXPIRED")), None)
    lam_data = None
    if final:
        fn = "expire" if final["to"] == "EXPIRED" else "payments"
        lam_data = {"transition": {"from": final["from"], "to": final["to"], "by": final["by"], "at_ms": final["at_ms"]},
                    "logs": _lambda_log(fn if final["by"] != "sweeper" else "sweeper", hold["hold_id"] if final["by"] != "sweeper" else '"fn"'),
                    "shard_after": shard}
    st["lambda"] = {"done": bool(final), "data": lam_data}
    found = eventlog.find(hold["hold_id"], hold["created_at_ms"]) if final else []
    st["s3"] = {"done": bool(final) and any(f["event"]["to_status"] == final["to"] for f in found),
                "data": {"bucket": eventlog.BUCKET, "objects": found} if found else None}
    duck = None
    if st["s3"]["done"]:
        try:
            duck = _duckdb(hold["sku"], hold["hold_id"])
        except FileNotFoundError:
            duck = None
    st["duckdb"] = {"done": bool(duck), "data": duck}

    stages = _order(MAIN, st)
    branch = None
    if run["scenario"] == "dlq":
        pm = run.get("payment_message") or {}
        mid = pm.get("message_id")
        fail = payments.failures(mid) if mid else None
        rec = dlq._find(mid) if mid else None
        audit = dlq.audit_log(message_id=mid) if mid else []
        b = {
            "bad": {"done": bool(pm), "data": pm and {"message": pm, "fault": faults.get(hold["hold_id"])}},
            "retry": {"done": bool(fail) and len(fail["attempts"]) >= 3, "progress": len(fail["attempts"]) if fail else 0,
                      "data": fail and {"attempts": fail["attempts"], "last_code": fail["last_code"]}},
            "dlq": {"done": bool(rec), "data": rec and {k: rec[k] for k in ("message_id", "code", "receive_count", "status", "archived_at_ms")}},
            "dryrun": {"done": any(a["action"] == "dry-run" for a in audit),
                       "data": next(({"by": a["actor"], "plan": a["plan"]} for a in audit if a["action"] == "dry-run"), None)},
            "replay": {"done": any(a["action"] == "replay" for a in audit),
                       "data": next(({"by": a["actor"], **(a["result"] or {})} for a in audit if a["action"] == "replay"), None)},
            "released": {"done": hold["status"] == "RELEASED", "data": final and {"transition": final, "shard_now": shard}},
        }
        branch = _order(BRANCH, b)   # in this scenario the main line's release *is* the replay's release
    return {"run": {k: v for k, v in run.items() if k not in ("PK", "SK")}, "hold": hold, "stages": stages,
            "branch": branch, "terminal": terminal, "ops_link": f"/ops?q={hold['hold_id']}"}
# @learn:end learn-trace


def _order(defs: list[dict], st: dict) -> list[dict]:
    out, active = [], False
    for d in defs:
        s = st[d["id"]]
        status = "done" if s.get("done") else "pending"
        if status == "pending" and not active:
            status, active = "active", True
        out.append({**d, "status": status, "data": s.get("data"), "progress": s.get("progress")})
    return out
