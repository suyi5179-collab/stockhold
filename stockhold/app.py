"""Flask application factory. Run: flask --app stockhold.app run --port 8840"""
from __future__ import annotations

from flask import Flask, jsonify

from . import api


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(api.bp)

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    return app


app = create_app()
