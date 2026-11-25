"""Users, roles, API tokens (JWT) and the ops tool's session login.

    role        can
    oncall      everything below, plus replay dead-lettered messages and arm faults
    viewer      search the DLQ, compare payloads, dry-run (never replay)
    storefront  the reservation API: reserve, confirm, release, payment events
"""
from __future__ import annotations

import functools
import os
import time

import jwt
from flask import g, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .aws import ddb
from .config import settings
from .dynamo import item, plain
from .states import StockholdError

ROLES = {"oncall", "viewer", "storefront"}


class Unauthorized(StockholdError):
    code, status = "UNAUTHORIZED", 401


class Forbidden(StockholdError):
    code, status = "FORBIDDEN", 403


def create_user(username: str, password: str, role: str) -> None:
    assert role in ROLES
    ddb().put_item(TableName=settings().table, Item=item({
        "PK": f"USER#{username}", "SK": "USER", "username": username, "role": role,
        "password_hash": generate_password_hash(password)}))


def check_login(username: str, password: str) -> dict | None:
    u = plain(ddb().get_item(TableName=settings().table, Key=item({"PK": f"USER#{username}", "SK": "USER"}),
                             ConsistentRead=True).get("Item"))
    if not u or not check_password_hash(u["password_hash"], password):
        return None
    return {"username": u["username"], "role": u["role"]}


def issue_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode({"sub": user["username"], "role": user["role"], "iat": now,
                       "exp": now + settings().token_ttl_seconds}, settings().secret, algorithm="HS256")


def current_user() -> dict | None:
    h = request.headers.get("Authorization", "")
    if h.startswith("Bearer "):
        try:
            c = jwt.decode(h[7:], settings().secret, algorithms=["HS256"])
        except jwt.PyJWTError as e:
            raise Unauthorized("invalid or expired token") from e
        return {"username": c["sub"], "role": c["role"]}
    if session.get("user"):
        return session["user"]
    return None


# @learn:require-role
def require(*roles: str):
    """Decorator: the caller must be logged in (session or Bearer token) with one of these roles."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if u is None:
                raise Unauthorized("log in first")
            if u["role"] not in roles:
                raise Forbidden(f"needs role {' or '.join(roles)}, you are {u['role']}")
            g.user = u
            return fn(*a, **kw)
        return wrapper
    return deco
# @learn:end require-role


DEMO_USERS = [  # local-dev identities with obvious placeholder passwords; created only with STOCKHOLD_SEED_DEMO=1
    ("oncall", "oncall-demo", "oncall"),
    ("viewer", "viewer-demo", "viewer"),
    ("storefront", "storefront-demo", "storefront"),
]


def seed_demo() -> None:
    if os.environ.get("STOCKHOLD_SEED_DEMO") != "1":
        return
    for u, p, r in DEMO_USERS:
        create_user(u, p, r)
