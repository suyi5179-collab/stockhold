"""HTTP contract (docs/api.md): status codes and error codes, through the Flask app."""
import uuid

import pytest

from stockhold.app import create_app


@pytest.fixture
def client():
    return create_app().test_client()


@pytest.fixture
def sku(client):
    s = f"api-{uuid.uuid4().hex[:8]}"
    assert client.post("/v1/skus", json={"sku": s, "total": 3}).status_code == 201
    return s


def reserve(client, sku, qty=1, key=None, order="o-1"):
    return client.post("/v1/reservations", json={"sku": sku, "qty": qty, "order_id": order},
                       headers={"Idempotency-Key": key or uuid.uuid4().hex})


def test_reserve_201_then_replay_200(client, sku):
    a = reserve(client, sku, key=f"k-{sku}")
    b = reserve(client, sku, key=f"k-{sku}")
    assert (a.status_code, b.status_code) == (201, 200)
    assert a.json["hold_id"] == b.json["hold_id"] and b.json["replayed"] is True


def test_error_codes(client, sku):
    assert client.post("/v1/reservations", json={"sku": sku, "qty": 1, "order_id": "o"}).json["error"]["code"] == "VALIDATION_ERROR"
    assert reserve(client, sku, qty=9).status_code == 409
    assert reserve(client, sku, qty=9).json["error"]["code"] == "INSUFFICIENT_STOCK"
    assert reserve(client, sku, qty="2").status_code == 400
    assert reserve(client, "nope-" + sku).json["error"]["code"] == "NOT_FOUND"
    assert client.get("/v1/reservations/h_missing").json["error"]["code"] == "HOLD_NOT_FOUND"
    r = reserve(client, sku, key=f"x-{sku}")
    assert r.status_code == 201
    assert reserve(client, sku, qty=2, key=f"x-{sku}").json["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert client.post("/v1/skus", json={"sku": sku, "total": 1}).status_code == 400


def test_confirm_then_release_is_invalid(client, sku):
    h = reserve(client, sku).json
    c = client.post(f"/v1/reservations/{h['hold_id']}/confirm")
    assert c.status_code == 200 and c.json["status"] == "CONFIRMED" and c.json["changed"] is True
    r = client.post(f"/v1/reservations/{h['hold_id']}/release")
    assert r.status_code == 409 and r.json["error"]["code"] == "INVALID_TRANSITION"
    assert client.get(f"/v1/skus/{sku}").json["confirmed"] == 1
