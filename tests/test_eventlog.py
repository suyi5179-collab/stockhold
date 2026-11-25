"""Event log: stream records -> events, the real Streams -> Lambda -> S3 path, and the DuckDB SQL."""
import json
import os
import time

from stockhold import analytics, eventlog, inventory
from stockhold.states import RELEASED

HERE = os.path.dirname(__file__)


def _rec(old, new, eid="e1"):
    from boto3.dynamodb.types import TypeSerializer
    from stockhold.dynamo import _dec
    ser = TypeSerializer()
    img = lambda d: {k: ser.serialize(_dec(v)) for k, v in d.items()} if d else None  # noqa: E731
    r = {"eventID": eid, "eventName": "MODIFY" if old else "INSERT", "dynamodb": {"NewImage": img(new)}}
    if old:
        r["dynamodb"]["OldImage"] = img(old)
    return r


HOLD = {"PK": "HOLD#h1", "SK": "HOLD", "hold_id": "h1", "sku": "tee", "shard": 0, "qty": 1, "order_id": "o1",
        "status": "HELD", "created_at_ms": 1000, "expires_at_ms": 11000, "hold_seconds": 10,
        "history": [{"from": None, "to": "HELD", "at_ms": 1000, "by": "api"}]}


def test_stream_records_become_events_only_on_status_change():
    ins = eventlog.to_event(_rec(None, HOLD))
    assert (ins["from_status"], ins["to_status"], ins["wait_ms"]) == (None, "HELD", 0)
    noise = {**HOLD, "expiry_message": {"message_id": "m"}}
    assert eventlog.to_event(_rec(HOLD, noise)) is None
    exp = {**HOLD, "status": "EXPIRED", "history": HOLD["history"] + [{"from": "HELD", "to": "EXPIRED", "at_ms": 12500, "by": "sweeper"}]}
    e = eventlog.to_event(_rec(HOLD, exp))
    assert (e["to_status"], e["lag_after_expiry_ms"], e["actor"]) == ("EXPIRED", 1500, "sweeper")
    assert eventlog.to_event(_rec(None, {"PK": "SKU#tee", "SK": "SHARD#00", "status": "x"})) is None
    assert eventlog.key_for(0, "r1") == "events/dt=1970-01-01/hour=00/r1.jsonl"


# @learn:test-eventlog-e2e
def test_hold_changes_land_in_s3_as_json_lines(new_sku):
    sku = new_sku(3)
    h = inventory.reserve(sku, 1, "o-1")
    inventory.release(h["hold_id"], reason="payment failed")
    found, t0 = [], time.time()
    while time.time() - t0 < 45:
        found = eventlog.find(h["hold_id"], h["created_at_ms"])
        if {f["event"]["to_status"] for f in found} >= {"HELD", RELEASED}:
            break
        time.sleep(1)
    got = sorted((f["event"]["from_status"] or "", f["event"]["to_status"]) for f in found)
    assert got == [("", "HELD"), ("HELD", RELEASED)], found
    assert all(f["key"].startswith("events/dt=") and "/hour=" in f["key"] for f in found)
# @learn:end test-eventlog-e2e


def test_duckdb_sql_on_a_fixed_event_log(tmp_path):
    """The SQL files against a hand-made log, so every number below is known in advance."""
    d = tmp_path / "events" / "dt=2026-01-01" / "hour=00"
    d.mkdir(parents=True)
    evs = []
    for i in range(10):
        base = {"hold_id": f"h{i}", "sku": "tee", "shard": 0, "qty": 1, "order_id": f"o{i}", "created_at_ms": 0,
                "expires_at_ms": 60_000, "hold_seconds": 60, "actor": "api", "lag_after_expiry_ms": None}
        evs.append({**base, "from_status": None, "to_status": "HELD", "at_ms": 0, "wait_ms": 0})
        if i < 6:
            evs.append({**base, "from_status": "HELD", "to_status": "CONFIRMED", "at_ms": (i + 1) * 5000, "wait_ms": (i + 1) * 5000})
        elif i < 8:
            evs.append({**base, "from_status": "HELD", "to_status": "RELEASED", "at_ms": 1000, "wait_ms": 1000})
        else:
            evs.append({**base, "from_status": "HELD", "to_status": "EXPIRED", "at_ms": 62_000, "wait_ms": 62_000,
                        "actor": "expiry-message", "lag_after_expiry_ms": 2000})
    (d / "x.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    rates = analytics.run(str(tmp_path), "rates_by_product")["rows"][0]
    assert (rates["holds"], rates["confirmed"], rates["released"], rates["expired"]) == (10, 6, 2, 2)
    assert (rates["expiry_rate"], rates["release_rate"]) == (0.2, 0.2)
    wait = analytics.run(str(tmp_path), "wait_before_confirm")["rows"][0]
    assert (wait["confirmed"], wait["p50_s"], wait["max_s"]) == (6, 17.5, 30.0)
    rec = analytics.run(str(tmp_path), "hold_timeout_recommendation", where_sku="tee")["rows"][0]
    assert rec["current_hold_s"] == 60 and rec["expired"] == 2 and rec["recommended_hold_s"] == 36
    lag = analytics.run(str(tmp_path), "release_window")["rows"][0]
    assert (lag["sku"], lag["actor"], lag["expired"], lag["p50_ms"]) == ("tee", "expiry-message", 2, 2000)
