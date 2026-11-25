"""Single-table key design. Every access pattern in docs/design.md maps to one of these keys.

    item            PK                   SK              GSI (GSI1 is sparse: only while HELD)
    ---------------------------------------------------------------------------------------
    product meta    SKU#<sku>            META
    stock shard     SKU#<sku>            SHARD#<k>
    hold            HOLD#<hold_id>       HOLD            GSI1 HELD#<bucket> / expires_at_ms
                                                         GSI2 SKU#<sku>#<shard> / created_at_ms
"""
from __future__ import annotations

import hashlib

GSI1 = "gsi1"
GSI2 = "gsi2"
# The sweeper's index is spread over a few partitions so "every open hold" is not one hot key.
HELD_BUCKETS = 4


def sku_pk(sku: str) -> str:
    return f"SKU#{sku}"


def shard_sk(k: int) -> str:
    return f"SHARD#{k:02d}"


def sku_shard_key(sku: str, shard: int) -> str:
    return f"SKU#{sku}#{shard:02d}"


def hold_pk(hold_id: str) -> str:
    return f"HOLD#{hold_id}"


def hold_id_for(client: str, idempotency_key: str) -> str:
    """Hold ids are derived from the idempotency key, so a retried request lands on the same item."""
    return "h_" + hashlib.sha256(f"{client}\x00{idempotency_key}".encode()).hexdigest()[:20]


def held_bucket(hold_id: str) -> str:
    n = int(hashlib.sha256(hold_id.encode()).hexdigest()[:8], 16) % HELD_BUCKETS
    return f"HELD#{n}"


def split_stock(total: int, shards: int) -> list[int]:
    """Split total units across shards; the first `total % shards` shards get one extra unit."""
    if shards < 1:
        raise ValueError("shards must be >= 1")
    base, extra = divmod(total, shards)
    return [base + (1 if k < extra else 0) for k in range(shards)]
