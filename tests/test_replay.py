"""The DLQ replay tool: archive, search, compare, dry-run, safe replay, roles, audit, alerts."""
import time
import uuid

import pytest

from stockhold import dlq, faults, inventory, payments
from stockhold.states import HELD, RELEASED

from .conftest import login, run_concurrently
from .test_expiry import wait_status
from .test_payments import find_in_dlq


@pytest.fixture(scope="module")
def dead():
    """Three dead-lettered messages: a transient fault (replayable), an unknown hold, malformed JSON."""
    sku = f"rp-{uuid.uuid4().hex[:8]}"
    inventory.create_sku(sku, 5, 1)
    hold = inventory.reserve(sku, 2, "o-rp", hold_seconds=3600)
    faults.arm(hold["hold_id"], 3)
    ids = {
        "fault": payments.publish("payment_failed", hold["hold_id"], extra={"order_id": "o-rp", "sku": sku, "qty": 2})["message_id"],
        "unknown": payments.publish("payment_failed", "h_missing_" + sku, extra={"sku": sku})["message_id"],
        "malformed": payments.publish_raw('{"type": "payment_failed", "sku": "%s", ' % sku)["message_id"],
    }
    find_in_dlq(set(ids.values()))
    return {"sku": sku, "hold": hold, "ids": ids}


def test_archive_joins_the_failure_reason(dead):
    rec = dlq._find(dead["ids"]["fault"])
    assert rec["code"] == "DOWNSTREAM_UNAVAILABLE" and rec["status"] == "OPEN"
    assert [a["receive_count"] for a in rec["attempts"]] == [1, 2, 3]
    assert rec["hold_id"] == dead["hold"]["hold_id"] and rec["sku"] == dead["sku"]
    assert dlq.sync() >= 0 and dlq._find(dead["ids"]["fault"])["SK"] == rec["SK"]   # archiving again is a no-op


def test_search_by_hold_sku_code_and_time(dead):
    hid = dead["hold"]["hold_id"]
    assert [m["message_id"] for m in dlq.search(hid)] == [dead["ids"]["fault"]]
    assert {m["message_id"] for m in dlq.search(dead["sku"])} == set(dead["ids"].values())
    by_code = {m["message_id"] for m in dlq.search(dead["sku"], code="MALFORMED_EVENT")}
    assert by_code == {dead["ids"]["malformed"]}
    assert dlq.search(dead["sku"], since_ms=inventory.now_ms() + 60_000) == []


def test_compare_message_with_current_hold_and_with_another_message(dead):
    d = dlq.detail(dead["ids"]["fault"])
    assert all(c["same"] for c in d["compare"]), d["compare"]
    diff = {x["path"]: x for x in dlq.compare(dead["ids"]["fault"], dead["ids"]["unknown"])}
    assert not diff["body.hold_id"]["same"] and not diff["code"]["same"] and diff["body.type"]["same"]


# @learn:test-replay-safety
def test_replay_is_dry_run_first_oncall_only_and_at_most_once(dead, api, users):
    mid, hid = dead["ids"]["fault"], dead["hold"]["hold_id"]
    plan = dlq.dry_run(mid)
    assert plan["allowed"] and plan["effect"]["transition"] == "HELD -> RELEASED"
    assert plan["effect"]["available_after"] == plan["effect"]["available_before"] + 2
    assert inventory.get_hold(hid)["status"] == HELD                       # dry-run changed nothing

    login(api, users, "viewer")
    assert api.post(f"/ops/api/messages/{mid}/dry-run").status_code == 200
    r = api.post(f"/ops/api/messages/{mid}/replay")
    assert r.status_code == 403 and r.json["error"]["code"] == "FORBIDDEN"

    login(api, users, "oncall")
    outcomes = run_concurrently(5, lambda i: api.post(f"/ops/api/messages/{mid}/replay").status_code)
    assert sorted(outcomes) == [200, 409, 409, 409, 409], outcomes       # five clicks, one replay
    wait_status(hid, RELEASED, timeout=30)
    rep = inventory.stock_report(dead["sku"])
    assert rep["available"] == 5 and rep["balanced"]
    assert dlq._find(mid)["status"] == "REPLAYED"
    actions = [a["action"] for a in dlq.audit_log(message_id=mid)]
    assert actions.count("replay") == 1 and actions.count("replay-refused") == 4 and "dry-run" in actions
# @learn:end test-replay-safety


def test_unreplayable_messages_are_blocked(dead, api, users):
    login(api, users, "oncall")
    for kind, reason in (("unknown", "does not exist"), ("malformed", "malformed")):
        mid = dead["ids"][kind]
        assert not dlq.dry_run(mid)["allowed"]
        r = api.post(f"/ops/api/messages/{mid}/replay")
        assert r.status_code == 409 and r.json["error"]["code"] == "REPLAY_BLOCKED"
        assert reason in r.json["error"]["message"]
        assert dlq._find(mid)["status"] == "OPEN"


def test_alerts_show_open_dead_letters(dead):
    a = dlq.alerts()
    assert a["open"] >= 2 and a["oldest_open_age_s"] > 0 and a["redriven_15m"] >= 3
    state = {x["name"]: x["state"] for x in a["alerts"]}
    assert state["DLQRedriveVolume"] in ("warning", "firing")
    later = {x["name"]: x["state"] for x in dlq.alerts(now_ms=inventory.now_ms() + 3600_000)["alerts"]}
    assert later["DLQOldestMessageAge"] == "firing"


def test_pages_need_login(api, users):
    assert api.get("/ops").status_code == 302
    assert api.get("/ops/api/messages").status_code == 401
    login(api, users, "viewer")
    page = api.get("/ops").data.decode()
    assert "DLQ 重放工具" in page and 'data-testid="replay-needs-role"' in page
    assert api.post("/login", data={"username": users["viewer"], "password": "nope"}).status_code == 401
