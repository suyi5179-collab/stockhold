"""Checkout simulator page: pick a product, fire N concurrent orders, watch stock and shards."""
from __future__ import annotations

import uuid

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from . import auth, flash, inventory, seed
from .states import ValidationError

bp = Blueprint("checkout", __name__)
WRITE = ("storefront", "oncall")


@bp.get("/checkout")
def page():
    if not session.get("user"):
        return redirect(url_for("ops.login_page", next="/checkout"))
    return render_template("checkout.html", skus=seed.DEMO_SKUS)


@bp.get("/checkout/api/stock/<sku>")
@auth.require(*WRITE, "viewer")
def stock(sku):
    return jsonify(inventory.stock_report(sku))


@bp.post("/checkout/api/new-sku")
@auth.require(*WRITE)
def new_sku():
    """A fresh product for one run (products are never reset in place, so runs never mix)."""
    d = request.get_json(silent=True) or {}
    shards, units = int(d.get("shards", 1)), int(d.get("units", 100))
    if not 1 <= shards <= 32 or not 1 <= units <= 5000:
        raise ValidationError("shards 1..32, units 1..5000")
    sku = f"sale-{shards}x-{uuid.uuid4().hex[:5]}"
    inventory.create_sku(sku, units, shards, f"秒杀 {units} 件 · {shards} 分片")
    return jsonify(inventory.stock_report(sku)), 201


@bp.post("/checkout/api/flash")
@auth.require(*WRITE)
def run_flash():
    d = request.get_json(silent=True) or {}
    orders, conc, qty = int(d.get("orders", 50)), int(d.get("concurrency", 16)), int(d.get("qty", 1))
    if not 1 <= orders <= 2000 or not 1 <= conc <= 64 or not 1 <= qty <= 10:
        raise ValidationError("orders 1..2000, concurrency 1..64, qty 1..10")
    r = flash.run(str(d.get("sku", "")), orders, conc, qty, client=g.user["username"])
    return jsonify(r)
