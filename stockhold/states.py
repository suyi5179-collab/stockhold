"""The hold state machine. Every transition is explicit; terminal states never move again."""
from __future__ import annotations

HELD, CONFIRMED, RELEASED, EXPIRED = "HELD", "CONFIRMED", "RELEASED", "EXPIRED"
TERMINAL = frozenset({CONFIRMED, RELEASED, EXPIRED})

# @learn:state-machine
TRANSITIONS = {
    (HELD, CONFIRMED): "confirm",   # payment succeeded
    (HELD, RELEASED): "release",    # payment failed, or the client cancelled
    (HELD, EXPIRED): "expire",      # nobody paid before expiresAt
}
# @learn:end state-machine


class StockholdError(Exception):
    """An error with a stable code and HTTP status; docs/api.md lists every code."""
    code = "INTERNAL"
    status = 500

    def __init__(self, message: str = "", **detail):
        super().__init__(message or self.code)
        self.message = message or self.code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, **({"detail": self.detail} if self.detail else {})}}


class ValidationError(StockholdError):
    code, status = "VALIDATION_ERROR", 400


class NotFound(StockholdError):
    code, status = "NOT_FOUND", 404


class HoldNotFound(StockholdError):
    code, status = "HOLD_NOT_FOUND", 404


class InsufficientStock(StockholdError):
    code, status = "INSUFFICIENT_STOCK", 409


class InvalidTransition(StockholdError):
    code, status = "INVALID_TRANSITION", 409


class HoldExpired(StockholdError):
    code, status = "HOLD_EXPIRED", 409


class IdempotencyKeyReused(StockholdError):
    code, status = "IDEMPOTENCY_KEY_REUSED", 422


class Contention(StockholdError):
    code, status = "CONTENTION", 503


def check_transition(frm: str, to: str) -> str:
    """Return the action name for a legal transition, or raise InvalidTransition."""
    action = TRANSITIONS.get((frm, to))
    if action is None:
        raise InvalidTransition(f"{frm} -> {to} is not allowed", from_state=frm, to_state=to)
    return action
