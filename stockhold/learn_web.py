"""Teaching-mode pages: /learn, /learn/interview, /learn/problems, and their JSON endpoints."""
from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, abort, jsonify, render_template, request, send_from_directory

from . import learn, learn_docs
from .config import git_sha, settings
from .states import ValidationError

bp = Blueprint("learn", __name__)
VIDEOS = Path(__file__).resolve().parent.parent / "docs" / "videos"


def _ctx(tab: str) -> dict:
    return {"tab": tab, "repo_url": settings().repo_url, "git_sha": git_sha(), "anchors": learn_docs.anchors(),
            "main": learn.MAIN, "branch": learn.BRANCH, "scenarios": learn.SCENARIOS,
            "jaeger_url": os.environ.get("STOCKHOLD_JAEGER_UI", "http://127.0.0.1:16691")}


@bp.get("/learn")
def page():
    return render_template("learn.html", **_ctx("flow"))


@bp.get("/learn/interview")
def interview():
    return render_template("learn_interview.html", doc=learn_docs.interview(), **_ctx("interview"))


@bp.get("/learn/problems")
def problems():
    return render_template("learn_problems.html", doc=learn_docs.problems(), **_ctx("problems"))


@bp.post("/learn/api/start")
def start():
    if os.environ.get("STOCKHOLD_LEARN_MODE", "1") != "1":
        abort(404)
    d = request.get_json(silent=True) or {}
    return jsonify(learn.start(str(d.get("scenario", "payment_failed")))), 201


@bp.get("/learn/api/runs/<run_id>")
def run(run_id):
    return jsonify(learn.trace(run_id))


@bp.get("/videos/<path:name>")
def video(name):
    """docs/videos is the static folder for the per-part and per-problem videos (committed later)."""
    if not name.endswith(".mp4"):
        raise ValidationError("videos only")
    return send_from_directory(VIDEOS, name, conditional=True)
