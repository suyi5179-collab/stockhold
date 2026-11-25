"""The on-call DLQ replay tool (Flask + plain JavaScript): pages and their JSON endpoints."""
from __future__ import annotations

import time

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for

from . import auth, dlq, faults, inventory, metrics, payments
from .states import StockholdError
from .states import ValidationError

bp = Blueprint("ops", __name__)
READ = ("viewer", "oncall")


@bp.get("/login")
def login_page():
    return render_template("login.html", next=request.args.get("next", "/ops"), error=None)


@bp.post("/login")
def login():
    u = auth.check_login(request.form.get("username", ""), request.form.get("password", ""))
    nxt = request.form.get("next") or "/ops"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/ops"
    if not u:
        return render_template("login.html", next=nxt, error="用户名或密码不对"), 401
    session.clear()
    session["user"] = u
    return redirect(nxt)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("ops.login_page"))


@bp.get("/ops")
def ops_page():
    u = session.get("user")
    if not u:
        return redirect(url_for("ops.login_page", next=request.full_path.rstrip("?")))
    return render_template("ops.html", user=u)


@bp.get("/ops/api/alerts")
@auth.require(*READ)
def alerts():
    return jsonify(dlq.alerts())


@bp.post("/ops/api/sync")
@auth.require(*READ)
def sync():
    return jsonify({"archived": dlq.sync()})


@bp.get("/ops/api/messages")
@auth.require(*READ)
def messages():
    a = request.args
    since = int(a.get("since_min") or 0)
    return jsonify({"items": dlq.search(a.get("q", ""), a.get("code", ""),
                                        since_ms=int(time.time() * 1000) - since * 60_000 if since else 0,
                                        status=a.get("status", ""))})


@bp.get("/ops/api/messages/<mid>")
@auth.require(*READ)
def message(mid):
    return jsonify(dlq.detail(mid))


@bp.get("/ops/api/compare")
@auth.require(*READ)
def compare():
    return jsonify({"diff": dlq.compare(request.args.get("a", ""), request.args.get("b", ""))})


@bp.post("/ops/api/messages/<mid>/dry-run")
@auth.require(*READ)
def dry_run(mid):
    p = dlq.dry_run(mid)
    dlq.audit(g.user["username"], "dry-run", dlq.detail(mid)["message"], p)
    return jsonify(p)


# @learn:replay-endpoint
@bp.post("/ops/api/messages/<mid>/replay")
@auth.require("oncall")
def replay(mid):
    try:
        r = dlq.replay(mid, actor=g.user["username"])
    except StockholdError as e:
        metrics.REPLAYS.labels(e.code.lower()).inc()
        raise
    metrics.REPLAYS.labels("replayed").inc()
    return jsonify(r)
# @learn:end replay-endpoint


@bp.get("/ops/api/audit")
@auth.require(*READ)
def audit():
    return jsonify({"items": dlq.audit_log(message_id=request.args.get("message_id", ""))})


@bp.post("/ops/api/drill")
@auth.require("oncall")
def drill():
    """Make a bad message on purpose, to practise (and to demo) the replay flow."""
    d = request.get_json(silent=True) or {}
    kind = d.get("kind")
    if kind == "malformed":
        return jsonify(payments.publish_raw('{"type": "payment_failed", "hold_id": ')), 202
    if kind == "unknown":
        return jsonify(payments.publish("payment_failed", "h_does_not_exist")), 202
    if kind == "fault":
        sku = d.get("sku") or "demo-tee"
        hold = inventory.reserve(sku, 1, f"drill-{int(time.time())}", hold_seconds=3600)
        faults.arm(hold["hold_id"], 3)
        sent = payments.publish("payment_failed", hold["hold_id"],
                                extra={"order_id": hold["order_id"], "sku": sku, "qty": 1})
        return jsonify({**sent, "hold_id": hold["hold_id"]}), 202
    raise ValidationError("kind must be malformed, unknown or fault")
