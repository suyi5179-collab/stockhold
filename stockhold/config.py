"""All configuration comes from the environment; every default is a local-dev placeholder."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    aws_endpoint: str = field(default_factory=lambda: _env("AWS_ENDPOINT_URL", "http://localhost:45671"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "us-east-1"))
    table: str = field(default_factory=lambda: _env("STOCKHOLD_TABLE", "stockhold"))
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
