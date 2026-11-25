"""All configuration comes from the environment; every default is a local-dev placeholder."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# The one place the GitHub account is written down. Everything else (learn page links, docs rendered
# by the app) is built from REPO_URL; the markdown docs use relative paths.
DEFAULT_REPO_URL = "https://github.com/suyi5179-collab/stockhold"


@lru_cache(maxsize=1)
def git_sha() -> str:
    """GIT_SHA if set, else the commit this checkout is on; links are pinned to it, never to main."""
    if os.environ.get("GIT_SHA"):
        return os.environ["GIT_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parent, capture_output=True,
                              text=True, check=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "HEAD"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    # Inside a LocalStack Lambda the endpoint is injected; on a laptop it is the compose port.
    aws_endpoint: str = field(default_factory=lambda: _env(
        "AWS_ENDPOINT_URL", "" if os.environ.get("AWS_LAMBDA_FUNCTION_NAME") else "http://localhost:45671"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))
    table: str = field(default_factory=lambda: _env("STOCKHOLD_TABLE", "stockhold"))
    payments_queue: str = field(default_factory=lambda: _env("STOCKHOLD_PAYMENTS_QUEUE", "stockhold-payment-events"))
    payments_dlq: str = field(default_factory=lambda: _env("STOCKHOLD_PAYMENTS_DLQ", "stockhold-payment-events-dlq"))
    # Signs API tokens and the ops tool's session cookie. The default is a dev placeholder.
    secret: str = field(default_factory=lambda: _env("STOCKHOLD_SECRET", "dev-only-placeholder-change-me-in-production"))
    token_ttl_seconds: int = field(default_factory=lambda: int(_env("STOCKHOLD_TOKEN_TTL", "3600")))
    repo_url: str = field(default_factory=lambda: _env("REPO_URL", DEFAULT_REPO_URL).rstrip("/"))
    background: bool = field(default_factory=lambda: _env("STOCKHOLD_BACKGROUND", "0") == "1")
    expiry_queue: str = field(default_factory=lambda: _env("STOCKHOLD_EXPIRY_QUEUE", "stockhold-hold-expiry"))
    # The sweeper only touches holds this far past expiresAt: the delayed message gets the first chance.
    sweep_grace_ms: int = field(default_factory=lambda: int(_env("STOCKHOLD_SWEEP_GRACE_MS", "5000")))
    default_hold_seconds: int = field(default_factory=lambda: int(_env("STOCKHOLD_HOLD_SECONDS", "600")))
    max_reserve_attempts: int = field(default_factory=lambda: int(_env("STOCKHOLD_MAX_ATTEMPTS", "12")))
    # Failure controls. They exist only so the tests can prove they would catch the bug:
    #   no_condition      reserve does read-modify-write without a condition (oversells)
    #   no_release_guard  release/expire return units without checking the hold is still HELD
    sabotage: frozenset = field(default_factory=lambda: frozenset(
        s.strip() for s in _env("STOCKHOLD_SABOTAGE", "").split(",") if s.strip()))


def settings() -> Settings:
    # Re-read on every call: tests flip env vars between scenarios.
    return Settings()
