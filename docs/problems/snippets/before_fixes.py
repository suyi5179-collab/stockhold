"""Before-fix versions (not imported). See README.md in this folder."""
# flake8: noqa


# q01: the idempotency test used a constant key.
# @learn:q01-before
def test_same_key_returns_the_same_hold(new_sku):
    sku = new_sku(5)
    a = inventory.reserve(sku, 2, "o-1", idempotency_key="k-1")
    b = inventory.reserve(sku, 2, "o-1", idempotency_key="k-1")
    assert a["hold_id"] == b["hold_id"] and b["replayed"] is True
    assert inventory.stock_report(sku)["available"] == 3
# @learn:end q01-before


# q03: the archive copied SQS's receive count as it was.
# @learn:q03-before
rec = {
    "message_id": mid, "raw": m["Body"], "code": fail.get("last_code", "UNKNOWN"),
    "receive_count": int(m["Attributes"].get("ApproximateReceiveCount", "0")),
    "archived_at_ms": now, "status": "OPEN",
}
# @learn:end q03-before


# q04: confirm only checked the state, not the clock.
# @learn:q04-before
update_hold = {
    "TableName": t, "Key": item({"PK": keys.hold_pk(hold_id), "SK": "HOLD"}),
    "UpdateExpression": "SET #s = :to, version = version + :one, history = list_append(history, :h)",
    "ConditionExpression": "#s = :held AND version = :v",
    "ExpressionAttributeNames": {"#s": "status"},
    "ExpressionAttributeValues": vals({":to": to, ":one": 1, ":h": [entry], ":held": HELD, ":v": hold["version"]}),
}
# @learn:end q04-before
