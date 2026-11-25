"""Products, stock shards and holds. Every write that moves stock is a conditional write."""
from __future__ import annotations

import hashlib
import json
import random
import time
import uuid

from botocore.exceptions import ClientError

from . import keys, states, tracing
from .aws import ddb
from .config import settings
from .dynamo import cancellation_codes, error_code, item, plain, vals
from .states import (HELD, Contention, HoldExpired, HoldNotFound, IdempotencyKeyReused, InsufficientStock,
                     InvalidTransition, NotFound, ValidationError)


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- products and shards

def create_sku(sku: str, total: int, shards: int = 1, name: str = "", price_cents: int = 0) -> dict:
    """Create a product and its stock shards. Fails if the product already exists."""
    if not sku or "#" in sku:
        raise ValidationError("sku must be non-empty and must not contain '#'")
    split = keys.split_stock(total, shards)
    t = settings().table
    meta = {"PK": keys.sku_pk(sku), "SK": "META", "sku": sku, "name": name or sku, "price_cents": price_cents,
            "capacity": total, "shards": shards, "created_at_ms": now_ms()}
    actions = [{"Put": {"TableName": t, "Item": item(meta), "ConditionExpression": "attribute_not_exists(PK)"}}]
    for k, units in enumerate(split):
        actions.append({"Put": {"TableName": t, "Item": item({
            "PK": keys.sku_pk(sku), "SK": keys.shard_sk(k), "sku": sku, "shard": k,
            "available": units, "capacity": units, "version": 0})}})
    try:
        ddb().transact_write_items(TransactItems=actions)
    except ClientError as e:
        if "ConditionalCheckFailed" in cancellation_codes(e):
            raise ValidationError(f"sku {sku} already exists") from e
        raise
    return meta


def get_sku(sku: str) -> dict:
    r = ddb().get_item(TableName=settings().table, Key=item({"PK": keys.sku_pk(sku), "SK": "META"}),
                       ConsistentRead=True)
    meta = plain(r.get("Item"))
    if meta is None:
        raise NotFound(f"sku {sku} not found")
    return meta


def read_shards(sku: str) -> list[dict]:
    r = ddb().query(TableName=settings().table, ConsistentRead=True,
                    KeyConditionExpression="PK = :pk AND begins_with(SK, :s)",
                    ExpressionAttributeValues=vals({":pk": keys.sku_pk(sku), ":s": "SHARD#"}))
    return [plain(i) for i in r["Items"]]


# ---------------------------------------------------------------- reserve

def request_hash(sku: str, qty: int, order_id: str) -> str:
    return hashlib.sha256(json.dumps([sku, qty, order_id]).encode()).hexdigest()[:16]


def _hold_item(hold_id, sku, shard, qty, order_id, hold_seconds, t_ms, reserve_detail, req_hash=None):
    return {
        "PK": keys.hold_pk(hold_id), "SK": "HOLD", "hold_id": hold_id, "sku": sku, "shard": shard, "qty": qty,
        "order_id": order_id, "request_hash": req_hash or request_hash(sku, qty, order_id), "status": HELD, "version": 1, "created_at_ms": t_ms,
        "expires_at_ms": t_ms + hold_seconds * 1000, "hold_seconds": hold_seconds,
        "history": [{"from": None, "to": HELD, "at_ms": t_ms, "by": "api"}],
        "reserve": reserve_detail,
        # Sparse index for the expiry sweeper: present only while HELD.
        "gsi1pk": keys.held_bucket(hold_id), "gsi1sk": t_ms + hold_seconds * 1000,
        # Holds by product, spread the same way the stock is (see docs/design.md, hot keys).
        "gsi2pk": keys.sku_shard_key(sku, shard), "gsi2sk": t_ms,
    }


def _existing(hold_id: str, req_hash: str) -> dict | None:
    """Idempotent replay: the same key with the same request returns the hold it created the first time."""
    # @learn:idempotent-replay
    try:
        h = get_hold(hold_id)
    except HoldNotFound:
        return None
    if h.get("request_hash") != req_hash:
        raise IdempotencyKeyReused("this Idempotency-Key was already used for a different request", hold_id=hold_id)
    return {**h, "replayed": True}
    # @learn:end idempotent-replay


def reserve(sku: str, qty: int, order_id: str, hold_seconds: int | None = None, hold_id: str | None = None,
            client: str = "anonymous", idempotency_key: str | None = None) -> dict:
    with tracing.span("inventory.reserve", sku=sku, qty=qty) as sp:
        h = _reserve(sku, qty, order_id, hold_seconds, hold_id, client, idempotency_key)
        sp.update(hold_id=h["hold_id"], shard=h["shard"], attempts=h["reserve"]["attempts"],
                  conflicts=h["reserve"]["conflicts"], replayed=bool(h.get("replayed")))
        return h


def _reserve(sku, qty, order_id, hold_seconds, hold_id, client, idempotency_key) -> dict:
    """Hold `qty` units of `sku` for an order. Returns the hold.

    With an idempotency key the hold id is derived from (client, key): a retried request finds the
    hold it already created instead of taking stock a second time.
    """
    if not isinstance(qty, int) or qty < 1 or qty > 100:
        raise ValidationError("qty must be an integer between 1 and 100")
    if not order_id:
        raise ValidationError("order_id is required")
    hold_seconds = hold_seconds or settings().default_hold_seconds
    req_hash = request_hash(sku, qty, order_id)
    if idempotency_key:
        hold_id = keys.hold_id_for(client, idempotency_key)
        prior = _existing(hold_id, req_hash)
        if prior:
            return prior
    hold_id = hold_id or "h_" + uuid.uuid4().hex[:20]
    shards = read_shards(sku)
    if not shards:
        raise NotFound(f"sku {sku} not found")
    if "no_condition" in settings().sabotage:
        return _reserve_unsafe(sku, qty, order_id, hold_seconds, hold_id, shards)
    attempts = conflicts = 0
    for _ in range(settings().max_reserve_attempts):
        candidates = [s for s in shards if s["available"] >= qty]
        if not candidates:
            raise InsufficientStock(f"not enough {sku} in stock", requested=qty,
                                    available=sum(s["available"] for s in shards))
        shard = random.choice(candidates)
        attempts += 1
        t_ms = now_ms()
        detail = {"shard": shard["shard"], "available_before": shard["available"], "version_before": shard["version"],
                  "available_after": shard["available"] - qty, "version_after": shard["version"] + 1,
                  "attempts": attempts, "conflicts": conflicts}
        hold = _hold_item(hold_id, sku, shard["shard"], qty, order_id, hold_seconds, t_ms, detail)
        try:
            # @learn:reserve-transact
            ddb().transact_write_items(TransactItems=[
                {"Update": {
                    "TableName": settings().table,
                    "Key": item({"PK": keys.sku_pk(sku), "SK": keys.shard_sk(shard["shard"])}),
                    "UpdateExpression": "SET available = available - :n, version = version + :one",
                    # The guarantee: never below zero, and nobody wrote this shard since we read it.
                    "ConditionExpression": "available >= :n AND version = :v",
                    "ExpressionAttributeValues": vals({":n": qty, ":v": shard["version"], ":one": 1}),
                }},
                {"Put": {
                    "TableName": settings().table, "Item": item(hold),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }},
            ])
            # @learn:end reserve-transact
            return public(hold)
        except ClientError as e:
            codes = cancellation_codes(e)
            if error_code(e) != "TransactionCanceledException":
                raise
            if len(codes) > 1 and codes[1] == "ConditionalCheckFailed":
                # The hold already exists: a concurrent copy of this same request won.
                prior = _existing(hold_id, req_hash)
                if prior:
                    return prior
            if codes[0] not in ("ConditionalCheckFailed", "TransactionConflict") and codes[1:2] != ["TransactionConflict"]:
                raise
            # Someone else moved this shard first: re-read and try again (optimistic concurrency).
            conflicts += 1
            time.sleep(random.uniform(0, 0.004 * min(conflicts, 8)))
            shards = read_shards(sku)
    raise Contention(f"gave up after {attempts} attempts", attempts=attempts, conflicts=conflicts)


def _reserve_unsafe(sku, qty, order_id, hold_seconds, hold_id, shards):
    """Failure control `no_condition`: the obvious read-check-write, with no condition on the write."""
    # @learn:reserve-unsafe
    shard = shards[0]
    if shard["available"] < qty:
        raise InsufficientStock(f"not enough {sku} in stock", requested=qty, available=shard["available"])
    time.sleep(0.002)  # the gap every real read-modify-write has between its read and its write
    ddb().update_item(TableName=settings().table,
                      Key=item({"PK": keys.sku_pk(sku), "SK": keys.shard_sk(shard["shard"])}),
                      UpdateExpression="SET available = :a, version = :v",
                      ExpressionAttributeValues=vals({":a": shard["available"] - qty, ":v": shard["version"] + 1}))
    # @learn:end reserve-unsafe
    t_ms = now_ms()
    hold = _hold_item(hold_id, sku, shard["shard"], qty, order_id, hold_seconds, t_ms,
                      {"shard": shard["shard"], "available_before": shard["available"], "version_before": shard["version"],
                       "available_after": shard["available"] - qty, "version_after": shard["version"] + 1,
                       "attempts": 1, "conflicts": 0, "unsafe": True})
    ddb().put_item(TableName=settings().table, Item=item(hold))
    return public(hold)


# ---------------------------------------------------------------- holds

def public(hold: dict) -> dict:
    return {k: v for k, v in hold.items() if k not in ("PK", "SK", "gsi1pk", "gsi1sk", "gsi2pk", "gsi2sk")}


def get_hold(hold_id: str) -> dict:
    r = ddb().get_item(TableName=settings().table, Key=item({"PK": keys.hold_pk(hold_id), "SK": "HOLD"}),
                       ConsistentRead=True)
    h = plain(r.get("Item"))
    if h is None:
        raise HoldNotFound(f"hold {hold_id} not found", hold_id=hold_id)
    return public(h)


def transition(hold_id: str, to: str, by: str, reason: str = "") -> dict:
    with tracing.span("hold.transition", hold_id=hold_id, to=to, by=by) as sp:
        r = _transition(hold_id, to, by, reason)
        sp["changed"] = r["changed"]
        return r


def _transition(hold_id: str, to: str, by: str, reason: str = "") -> dict:
    """Move a HELD hold to CONFIRMED, RELEASED or EXPIRED.

    Returns {"hold": ..., "changed": bool}. Repeating a transition that already happened is a no-op
    (changed=False), so duplicate messages are harmless; any other illegal move raises.
    """
    hold = get_hold(hold_id)
    if hold["status"] == to:
        return {"hold": hold, "changed": False}
    states.check_transition(hold["status"], to)
    t_ms = now_ms()
    if to == states.CONFIRMED and t_ms >= hold["expires_at_ms"]:
        raise HoldExpired(f"hold {hold_id} expired before it was confirmed", hold_id=hold_id)
    entry = {"from": hold["status"], "to": to, "at_ms": t_ms, "by": by, **({"reason": reason} if reason else {})}
    t = settings().table
    # @learn:transition-guard
    update_hold = {
        "TableName": t, "Key": item({"PK": keys.hold_pk(hold_id), "SK": "HOLD"}),
        "UpdateExpression": "SET #s = :to, version = version + :one, history = list_append(history, :h) REMOVE gsi1pk, gsi1sk",
        "ConditionExpression": "#s = :held AND version = :v" + (" AND expires_at_ms > :now" if to == states.CONFIRMED else ""),
        "ExpressionAttributeNames": {"#s": "status"},
        "ExpressionAttributeValues": vals({":to": to, ":one": 1, ":h": [entry], ":held": HELD, ":v": hold["version"],
                                           **({":now": t_ms} if to == states.CONFIRMED else {})}),
    }
    # @learn:end transition-guard
    if to != states.CONFIRMED and "no_release_guard" in settings().sabotage:
        return _give_back_unsafe(hold, update_hold, t)
    try:
        if to == states.CONFIRMED:
            ddb().update_item(**update_hold)
        else:
            # Releasing and expiring give the units back to the shard the hold came from, in the same transaction.
            # @learn:release-transact
            ddb().transact_write_items(TransactItems=[
                {"Update": update_hold},
                {"Update": {"TableName": t, "Key": item({"PK": keys.sku_pk(hold["sku"]), "SK": keys.shard_sk(hold["shard"])}),
                            "UpdateExpression": "SET available = available + :n, version = version + :one",
                            "ConditionExpression": "attribute_exists(PK)",
                            "ExpressionAttributeValues": vals({":n": hold["qty"], ":one": 1})}},
            ])
            # @learn:end release-transact
    except ClientError as e:
        if error_code(e) not in ("ConditionalCheckFailedException", "TransactionCanceledException"):
            raise
        # Lost a race: look at what won.
        cur = get_hold(hold_id)
        if cur["status"] == to:
            return {"hold": cur, "changed": False}
        if to == states.CONFIRMED and cur["status"] == HELD:
            raise HoldExpired(f"hold {hold_id} expired before it was confirmed", hold_id=hold_id) from e
        raise InvalidTransition(f"hold {hold_id} is {cur['status']}", from_state=cur["status"], to_state=to) from e
    return {"hold": get_hold(hold_id), "changed": True}


def _give_back_unsafe(hold: dict, update_hold: dict, t: str) -> dict:
    """Failure control `no_release_guard`: two separate writes, neither checks the hold is still HELD."""
    # @learn:release-unsafe
    entry = update_hold["ExpressionAttributeValues"][":h"]
    ddb().update_item(TableName=t, Key=update_hold["Key"], ExpressionAttributeNames={"#s": "status"},
                      UpdateExpression="SET #s = :to, history = list_append(history, :h) REMOVE gsi1pk, gsi1sk",
                      ExpressionAttributeValues={":to": update_hold["ExpressionAttributeValues"][":to"], ":h": entry})
    ddb().update_item(TableName=t, Key=item({"PK": keys.sku_pk(hold["sku"]), "SK": keys.shard_sk(hold["shard"])}),
                      UpdateExpression="SET available = available + :n",
                      ExpressionAttributeValues=vals({":n": hold["qty"]}))
    # @learn:end release-unsafe
    return {"hold": get_hold(hold["hold_id"]), "changed": True}


def confirm(hold_id: str, by: str = "api") -> dict:
    return transition(hold_id, states.CONFIRMED, by)


def release(hold_id: str, by: str = "api", reason: str = "") -> dict:
    return transition(hold_id, states.RELEASED, by, reason)


def expire(hold_id: str, by: str = "expirer") -> dict:
    return transition(hold_id, states.EXPIRED, by)


# ---------------------------------------------------------------- invariants

def holds_for_sku(sku: str) -> list[dict]:
    """All holds of one product: one query per stock shard on GSI2 (eventually consistent)."""
    out = []
    for k in range(get_sku(sku)["shards"]):
        kw = {}
        while True:
            r = ddb().query(TableName=settings().table, IndexName=keys.GSI2,
                            KeyConditionExpression="gsi2pk = :p",
                            ExpressionAttributeValues=vals({":p": keys.sku_shard_key(sku, k)}), **kw)
            out += [public(plain(i)) for i in r["Items"]]
            if "LastEvaluatedKey" not in r:
                break
            kw = {"ExclusiveStartKey": r["LastEvaluatedKey"]}
    return out


def stock_report(sku: str) -> dict:
    """The invariant: available + held + confirmed == capacity, and nothing ever goes below zero."""
    meta = get_sku(sku)
    shards = read_shards(sku)
    holds = holds_for_sku(sku)
    by = {s: sum(h["qty"] for h in holds if h["status"] == s) for s in (HELD, states.CONFIRMED, states.RELEASED, states.EXPIRED)}
    available = sum(s["available"] for s in shards)
    sold = by[HELD] + by[states.CONFIRMED]
    return {
        "sku": sku, "capacity": meta["capacity"], "available": available,
        "held": by[HELD], "confirmed": by[states.CONFIRMED], "released": by[states.RELEASED], "expired": by[states.EXPIRED],
        "holds": len(holds), "oversold": max(0, sold - meta["capacity"]),
        "balanced": available + sold == meta["capacity"],
        "negative_shards": [s["shard"] for s in shards if s["available"] < 0],
        "shards": [{"shard": s["shard"], "available": s["available"], "capacity": s["capacity"], "version": s["version"]} for s in shards],
    }
