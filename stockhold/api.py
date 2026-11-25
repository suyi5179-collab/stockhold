"""The reservation API (contract: docs/api.md)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from . import expiry, inventory
from .states import StockholdError, ValidationError

bp = Blueprint("api", __name__, url_prefix="/v1")


def body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")
    return data


def client_id() -> str:
    """Who is calling. Until authentication exists (step 05) everyone is the same client."""
    return "anonymous"


@bp.errorhandler(StockholdError)
def _err(e: StockholdError):
    return jsonify(e.to_dict()), e.status


@bp.post("/skus")
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
def get_sku(sku):
    return jsonify(inventory.stock_report(sku))


@bp.post("/reservations")
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
def get_hold(hold_id):
    return jsonify(inventory.get_hold(hold_id))


@bp.post("/reservations/<hold_id>/confirm")
def confirm(hold_id):
    r = inventory.confirm(hold_id)
    return jsonify({**r["hold"], "changed": r["changed"]})


@bp.post("/reservations/<hold_id>/release")
def release(hold_id):
    r = inventory.release(hold_id, by="api", reason="client cancelled")
    return jsonify({**r["hold"], "changed": r["changed"]})
