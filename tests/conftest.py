"""Integration tests run against LocalStack (docker-compose -p stockhold up -d && ./scripts/deploy.sh)."""
from __future__ import annotations

import os
import threading
import uuid

import pytest

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:45671")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")


def pytest_sessionstart(session):
    from stockhold.aws import ddb
    from stockhold.config import settings
    try:
        ddb().describe_table(TableName=settings().table)
    except Exception as e:  # noqa: BLE001
        pytest.exit(f"table {settings().table} not reachable ({e.__class__.__name__}); "
                    "run docker-compose -p stockhold up -d --wait && ./scripts/deploy.sh", returncode=2)


TEST_PASSWORD = "test-only-pass"   # test fixture value, not a secret


@pytest.fixture(scope="session")
def users():
    from stockhold import auth
    names = {"oncall": "t-oncall", "viewer": "t-viewer", "storefront": "t-store"}
    for role, name in names.items():
        auth.create_user(name, TEST_PASSWORD, role)
    return names


@pytest.fixture
def api(users):
    """Flask test client plus a helper that adds a Bearer token for a role."""
    from stockhold.app import create_app
    c = create_app().test_client()
    tokens = {}

    def headers(role="storefront", **extra):
        if role not in tokens:
            r = c.post("/v1/auth/token", json={"username": users[role], "password": TEST_PASSWORD})
            tokens[role] = r.json["access_token"]
        return {"Authorization": f"Bearer {tokens[role]}", **extra}
    c.auth = headers
    return c


def login(client, users, role):
    r = client.post("/login", data={"username": users[role], "password": TEST_PASSWORD, "next": "/ops"})
    assert r.status_code == 302, r.data


@pytest.fixture
def new_sku():
    from stockhold import inventory

    def make(total: int, shards: int = 1, prefix: str = "t") -> str:
        sku = f"{prefix}-{uuid.uuid4().hex[:10]}"
        inventory.create_sku(sku, total, shards)
        return sku
    return make


def run_concurrently(n: int, fn):
    """Start n threads on a barrier so they really race; return each call's result or exception."""
    barrier = threading.Barrier(n)
    out: list = [None] * n

    def worker(i):
        barrier.wait()
        try:
            out[i] = fn(i)
        except Exception as e:  # noqa: BLE001
            out[i] = e
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out
