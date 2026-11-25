#!/usr/bin/env python3
"""Hot key benchmark: the same flash sale on 1 shard vs N shards, against LocalStack's DynamoDB.

LocalStack does not model partition throughput limits, so this cannot show throttling. What it does
show is optimistic-concurrency contention on one hot item: conflicts per reservation and tail latency.

    .venv/bin/python scripts/bench_shards.py --orders 400 --concurrency 32 --rounds 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:45671")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from stockhold import flash, inventory  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--orders", type=int, default=400)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--shards", default="1,8")
    ap.add_argument("--out", default="docs/results/bench_shards.json")
    a = ap.parse_args()
    rows = []
    for rnd in range(a.rounds):
        for shards in [int(s) for s in a.shards.split(",")]:
            sku = f"bench-{shards}-{uuid.uuid4().hex[:6]}"
            inventory.create_sku(sku, a.orders, shards)          # exactly enough stock for every order
            r = flash.run(sku, a.orders, a.concurrency)
            row = {k: r[k] for k in ("shards", "orders", "concurrency", "held", "sold_out", "contention",
                                     "conflicts_total", "conflicts_per_hold", "max_attempts", "latency_ms",
                                     "wall_s", "holds_per_s", "oversold", "balanced")}
            row["round"] = rnd + 1
            rows.append(row)
            print(json.dumps(row))
    with open(a.out, "w") as f:
        json.dump(rows, f, indent=1)
        f.write("\n")
    print(f"\n| round | shards | held | contention (gave up) | conflicts / hold | p50 ms | p95 ms | p99 ms | holds/s | oversold |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        l = r["latency_ms"]
        print(f"| {r['round']} | {r['shards']} | {r['held']}/{r['orders']} | {r['contention']} | {r['conflicts_per_hold']} | "
              f"{l['p50']} | {l['p95']} | {l['p99']} | {r['holds_per_s']} | {r['oversold']} |")


if __name__ == "__main__":
    main()
