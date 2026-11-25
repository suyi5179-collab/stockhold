"""Flask application factory. Run: flask --app stockhold.app run --port 8840"""
from __future__ import annotations

import logging
import threading
import time

from flask import Flask, Response, g, jsonify, request

from . import api, auth, checkout, metrics, ops, tracing
from .config import settings
from .states import StockholdError

log = logging.getLogger("stockhold.app")


def _background_sync() -> None:
    """Archive dead letters every few seconds, so alerts in the tool are minutes-fresh at worst."""
    from . import dlq
    while True:
        try:
            dlq.sync()
        except Exception:  # noqa: BLE001
            log.exception("dlq sync failed")
        time.sleep(3)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = settings().secret
    tracing.setup_sdk()
    metrics.register()

    # @learn:request-span
    @app.before_request
    def _start_span():
        if request.path.startswith(("/static", "/metrics", "/healthz", "/videos")):
            return
        cm = tracing.span(f"{request.method} {request.url_rule.rule if request.url_rule else request.path}",
                          **{"http.method": request.method, "http.target": request.path})
        g._span = (cm, cm.__enter__())

    @app.teardown_request
    def _end_span(exc):
        sp = g.pop("_span", None)
        if sp:
            sp[0].__exit__(None, None, None)
    # @learn:end request-span

    @app.get("/metrics")
    def prom():
        body, ctype = metrics.exposition()
        return Response(body, content_type=ctype)
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.register_blueprint(api.bp)
    app.register_blueprint(ops.bp)
    app.register_blueprint(checkout.bp)

    @app.errorhandler(StockholdError)
    def _err(e: StockholdError):
        return jsonify(e.to_dict()), e.status

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/")
    def index():
        from flask import redirect
        return redirect("/learn")

    if settings().background:
        threading.Thread(target=_background_sync, daemon=True, name="dlq-sync").start()
    return app
