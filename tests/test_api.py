"""HTTP contract (docs/api.md): status codes and error codes, through the Flask app."""
import uuid

import pytest


@pytest.fixture
def sku(api):
    s = f"api-{uuid.uuid4().hex[:8]}"
    assert api.post("/v1/skus", json={"sku": s, "total": 3}, headers=api.auth("oncall")).status_code == 201
    return s


def reserve(api, sku, qty=1, key=None, order="o-1", role="storefront"):
    return api.post("/v1/reservations", json={"sku": sku, "qty": qty, "order_id": order},
                    headers=api.auth(role, **{"Idempotency-Key": key or uuid.uuid4().hex}))


def test_reserve_201_then_replay_200(api, sku):
    a = reserve(api, sku, key=f"k-{sku}")
    b = reserve(api, sku, key=f"k-{sku}")
    assert (a.status_code, b.status_code) == (201, 200)
    assert a.json["hold_id"] == b.json["hold_id"] and b.json["replayed"] is True
    assert a.json["expiry_message"]["body"]["hold_id"] == a.json["hold_id"]


def test_error_codes(api, sku):
    r = api.post("/v1/reservations", json={"sku": sku, "qty": 1, "order_id": "o"}, headers=api.auth())
    assert r.json["error"]["code"] == "VALIDATION_ERROR"
    assert reserve(api, sku, qty=9).status_code == 409
    assert reserve(api, sku, qty=9).json["error"]["code"] == "INSUFFICIENT_STOCK"
    assert reserve(api, sku, qty="2").status_code == 400
    assert reserve(api, "nope-" + sku).json["error"]["code"] == "NOT_FOUND"
    assert api.get("/v1/reservations/h_missing", headers=api.auth()).json["error"]["code"] == "HOLD_NOT_FOUND"
    assert reserve(api, sku, key=f"x-{sku}").status_code == 201
    assert reserve(api, sku, qty=2, key=f"x-{sku}").json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert api.post("/v1/skus", json={"sku": sku, "total": 1}, headers=api.auth("oncall")).status_code == 400


def test_confirm_then_release_is_invalid(api, sku):
    h = reserve(api, sku).json
    c = api.post(f"/v1/reservations/{h['hold_id']}/confirm", headers=api.auth())
    assert c.status_code == 200 and c.json["status"] == "CONFIRMED" and c.json["changed"] is True
    r = api.post(f"/v1/reservations/{h['hold_id']}/release", headers=api.auth())
    assert r.status_code == 409 and r.json["error"]["code"] == "INVALID_TRANSITION"
    assert api.get(f"/v1/skus/{sku}", headers=api.auth()).json["confirmed"] == 1


# @learn:test-auth
def test_authentication_and_roles(api, sku):
    body = {"sku": sku, "qty": 1, "order_id": "o"}
    r = api.post("/v1/reservations", json=body, headers={"Idempotency-Key": "k"})
    assert r.status_code == 401 and r.json["error"]["code"] == "UNAUTHORIZED"
    r = api.post("/v1/reservations", json=body, headers={"Idempotency-Key": "k", "Authorization": "Bearer forged.jwt.value"})
    assert r.status_code == 401
    assert reserve(api, sku, role="viewer").json["error"]["code"] == "FORBIDDEN"       # viewers cannot order
    assert api.post("/v1/skus", json={"sku": sku + "x", "total": 1}, headers=api.auth()).status_code == 403
    bad = api.post("/v1/auth/token", json={"username": "t-store", "password": "wrong"})
    assert bad.status_code == 401
# @learn:end test-auth


def test_payment_event_endpoint_enqueues(api, sku):
    h = reserve(api, sku).json
    r = api.post("/v1/payments/events", json={"type": "payment_failed", "hold_id": h["hold_id"]}, headers=api.auth())
    assert r.status_code == 202 and r.json["message_id"]
    assert api.post("/v1/payments/events", json={"type": "refund", "hold_id": "x"}, headers=api.auth()).status_code == 400
