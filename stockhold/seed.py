"""Demo data: placeholder users (only with STOCKHOLD_SEED_DEMO=1) and two products. Idempotent.

    .venv/bin/python -m stockhold.seed
"""
from __future__ import annotations

from . import auth, inventory
from .states import ValidationError

DEMO_SKUS = [
    # sku, units, shards, name, price
    ("demo-tee", 500, 1, "T 恤（普通商品，一个分片）", 1900),
    ("flash-sneaker", 400, 8, "限量球鞋（秒杀商品，八个分片）", 12900),
]


def main() -> None:
    auth.seed_demo()
    for sku, units, shards, name, price in DEMO_SKUS:
        try:
            inventory.create_sku(sku, units, shards, name, price)
            print(f"created {sku}: {units} units in {shards} shard(s)")
        except ValidationError:
            print(f"{sku} exists")


if __name__ == "__main__":
    main()
