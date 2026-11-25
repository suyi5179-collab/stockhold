# stockhold API

Base URL `http://127.0.0.1:8840`. JSON in, JSON out. Every error has the same shape:

```json
{"error": {"code": "INSUFFICIENT_STOCK", "message": "not enough tee in stock", "detail": {"requested": 3, "available": 1}}}
```

## Authentication

`POST /v1/auth/token` `{"username", "password"}` → `200 {"access_token", "token_type": "Bearer", "role"}` (HS256 JWT, 1 h).
Send `Authorization: Bearer <token>` on every `/v1` call below. The ops tool (`/ops`) uses a session cookie from `POST /login` instead.

| role | may call |
|---|---|
| `storefront` | reservations, payment events, read products |
| `oncall` | everything, including `POST /v1/skus`, replay, drills |
| `viewer` | the ops tool read-only: search, compare, dry-run |

## Holds

A hold is in exactly one state. Only these transitions exist; terminal states never move again.

```
HELD ──confirm──▶ CONFIRMED      payment succeeded (only while expires_at_ms > now)
HELD ──release──▶ RELEASED       payment failed, or the client cancelled
HELD ──expire───▶ EXPIRED        expires_at_ms passed (expiry message or sweeper)
```

Repeating a transition that already happened is a successful no-op (`"changed": false`); any other move is `409 INVALID_TRANSITION`.

## Endpoints

| method & path | body / headers | success | errors |
|---|---|---|---|
| `POST /v1/skus` (oncall) | `{"sku", "total", "shards": 1..32, "name"?, "price_cents"?}` | `201` product | `400 VALIDATION_ERROR` (also: sku exists) |
| `GET /v1/skus/{sku}` | | `200` stock report: `capacity, available, held, confirmed, released, expired, oversold, balanced, shards[]` | `404 NOT_FOUND` |
| `POST /v1/reservations` | header `Idempotency-Key` (1–128 chars, required); `{"sku", "qty": 1..100, "order_id", "hold_seconds"?: 1..86400}` | `201` new hold; `200` same key + same body → the original hold with `"replayed": true` | `400 VALIDATION_ERROR`, `404 NOT_FOUND`, `409 INSUFFICIENT_STOCK`, `422 IDEMPOTENCY_KEY_REUSED` (same key, different body), `503 CONTENTION` (gave up after `STOCKHOLD_MAX_ATTEMPTS` optimistic retries; safe to retry with the same key) |
| `GET /v1/reservations/{hold_id}` | | `200` hold | `404 HOLD_NOT_FOUND` |
| `POST /v1/reservations/{hold_id}/confirm` | | `200` hold + `changed` | `404 HOLD_NOT_FOUND`, `409 INVALID_TRANSITION`, `409 HOLD_EXPIRED` |
| `POST /v1/reservations/{hold_id}/release` | | `200` hold + `changed` | `404 HOLD_NOT_FOUND`, `409 INVALID_TRANSITION` |
| `POST /v1/payments/events` | `{"type": "payment_failed" \| "payment_succeeded", "hold_id", "event_id"?, "order_id"?, "sku"?, "qty"?}` | `202 {"message_id"}` — queued for the payments Lambda | `400 VALIDATION_ERROR` |

All `/v1` endpoints: `401 UNAUTHORIZED` without a valid token, `403 FORBIDDEN` with the wrong role.

### The hold object

```json
{"hold_id": "h_697a…", "sku": "tee", "shard": 0, "qty": 2, "order_id": "o-1001", "status": "HELD", "version": 1,
 "created_at_ms": 1795629000000, "expires_at_ms": 1795629600000, "hold_seconds": 600, "request_hash": "…",
 "history": [{"from": null, "to": "HELD", "at_ms": 1795629000000, "by": "api"}],
 "reserve": {"shard": 0, "available_before": 5, "available_after": 3, "version_before": 0, "version_after": 1, "attempts": 1, "conflicts": 0},
 "expiry_message": {"message_id": "…", "delay_seconds": 600, "queue": "stockhold-hold-expiry", "body": {…}}}
```

## Messages

| queue | body | consumer | on failure |
|---|---|---|---|
| `stockhold-hold-expiry` | `{"type": "hold.expire", "hold_id", "sku", "expires_at_ms"}`, `DelaySeconds` = time left (max 900) | `stockhold-expire` Lambda | 5 receives → `stockhold-hold-expiry-dlq`; the sweeper covers it |
| `stockhold-payment-events` | `{"type", "hold_id", "event_id", …}` | `stockhold-payments` Lambda | every attempt recorded as `FAIL#<message_id>`; 3 receives → `stockhold-payment-events-dlq` |

Failure codes recorded for payment events: `MALFORMED_EVENT`, `HOLD_NOT_FOUND`, `DOWNSTREAM_UNAVAILABLE` (fault switch), `HOLD_EXPIRED` (paid after expiry), `INVALID_TRANSITION`.

## Ops tool (`/ops`, session cookie)

| method & path | role | what |
|---|---|---|
| `GET /ops/api/alerts` | viewer, oncall | `DLQOldestMessageAge` (firing > 300 s), `DLQRedriveVolume` (warning ≥ 1, firing ≥ 5 in 15 min), queue depth |
| `POST /ops/api/sync` | viewer, oncall | archive everything in the DLQ now (the app also does it every 3 s) |
| `GET /ops/api/messages?q=&code=&status=&since_min=` | viewer, oncall | search archived dead letters (q: hold id, sku, message id, raw body) |
| `GET /ops/api/messages/{id}` | viewer, oncall | body, every failed attempt, the hold now, field comparison, replay plan |
| `GET /ops/api/compare?a=&b=` | viewer, oncall | field diff of two dead letters |
| `POST /ops/api/messages/{id}/dry-run` | viewer, oncall | `{"allowed", "effect" \| "reason", "warning"}`; audited, writes nothing else |
| `POST /ops/api/messages/{id}/replay` | **oncall** | `200 {"replay_id", "new_message_id", …}`; `409 REPLAY_BLOCKED` (plan not allowed), `409 ALREADY_REPLAYED` |
| `GET /ops/api/audit?message_id=` | viewer, oncall | dry-runs, replays and refused replays |
| `POST /ops/api/drill` `{"kind": "fault" \| "unknown" \| "malformed"}` | oncall | create a bad message on purpose |
