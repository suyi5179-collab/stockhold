"""The reservation API (contract: docs/api.md)."""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from . import auth, expiry, inventory, payments
from .states import ValidationError

bp = Blueprint("api", __name__, url_prefix="/v1")


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data


WRITE = ("storefront", "oncall")


def client_id() -> str:
    """Idempotency keys are scoped to the caller, so two shops can both send key "1"."""
    return g.user["username"]


@bp.post("/auth/token")
def token():
    d = body()
    u = auth.check_login(str(d.get("username", "")), str(d.get("password", "")))
    if not u:
        raise auth.Unauthorized("bad username or password")
    return jsonify({"access_token": auth.issue_token(u), "token_type": "Bearer", "role": u["role"]})


@bp.post("/skus")
@auth.require("oncall")
def create_sku():
    d = body()
    try:
        total, shards = int(d.get("total", 0)), int(d.get("shards", 1))
    except (TypeError, ValueError) as e:
        raise ValidationError("total and shards must be integers") from e
    if total < 0 or not 1 <= shards <= 32:
        raise ValidationError("total must be >= 0 and shards between 1 and 32")
    return jsonify(inventory.create_sku(str(d.get("sku", "")), total, shards, d.get("name", ""),
                                        int(d.get("price_cents", 0)))), 201


@bp.get("/skus/<sku>")
@auth.require(*WRITE)
def get_sku(sku):
    return jsonify(inventory.stock_report(sku))


@bp.post("/reservations")
@auth.require(*WRITE)
def reserve():
    d = body()
    qty = d.get("qty")
    if not isinstance(qty, int) or isinstance(qty, bool):
        raise ValidationError("qty must be an integer")
    hold_seconds = d.get("hold_seconds")
    if hold_seconds is not None and (not isinstance(hold_seconds, int) or not 1 <= hold_seconds <= 86400):
        raise ValidationError("hold_seconds must be an integer between 1 and 86400")
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise ValidationError("an Idempotency-Key header (1-128 chars) is required")
    hold = inventory.reserve(str(d.get("sku", "")), qty, str(d.get("order_id", "")), hold_seconds,
                             client=client_id(), idempotency_key=key)
    # @learn:api-schedule
    # A crash between the reserve and this send leaves a hold with no expiry message. A retry of the
    # same request heals it here; if nobody retries, the sweeper expires it.
    if hold["status"] == "HELD" and not hold.get("expiry_message"):
        hold["expiry_message"] = expiry.schedule(hold)
    # @learn:end api-schedule
    return jsonify(hold), 200 if hold.get("replayed") else 201


@bp.get("/reservations/<hold_id>")
@auth.require(*WRITE)
def get_hold(hold_id):
    return jsonify(inventory.get_hold(hold_id))


@bp.post("/reservations/<hold_id>/confirm")
@auth.require(*WRITE)
def confirm(hold_id):
    r = inventory.confirm(hold_id)
    return jsonify({**r["hold"], "changed": r["changed"]})


@bp.post("/reservations/<hold_id>/release")
@auth.require(*WRITE)
def release(hold_id):
    r = inventory.release(hold_id, by="api", reason="client cancelled")
    return jsonify({**r["hold"], "changed": r["changed"]})


@bp.post("/payments/events")
@auth.require(*WRITE)
def payment_event():
    """What the payment provider's webhook would do: put the event on the payment-events queue."""
    d = body()
    if d.get("type") not in payments.TYPES or not d.get("hold_id"):
        raise ValidationError("type must be payment_failed or payment_succeeded, and hold_id is required")
    r = payments.publish(d["type"], str(d["hold_id"]), d.get("event_id"),
                         extra={k: d[k] for k in ("order_id", "sku", "qty") if k in d})
    return jsonify(r), 202
