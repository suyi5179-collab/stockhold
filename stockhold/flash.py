"""Flash-sale driver: many concurrent checkouts against one product. Used by the checkout page,
scripts/bench_shards.py and the tests, so the page shows exactly what the benchmark measures."""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import inventory
from .states import Contention, InsufficientStock


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)


# @learn:flash-sale
def run(sku: str, orders: int, concurrency: int, qty: int = 1, client: str = "flash") -> dict:
    """Fire `orders` reservations with `concurrency` threads. Returns outcome counts and latency."""
    before = inventory.stock_report(sku)
    batch = uuid.uuid4().hex[:6]

    def one(i: int):
        t0 = time.perf_counter()
        try:
            h = inventory.reserve(sku, qty, f"flash-{batch}-{i}", client=client, idempotency_key=f"{batch}-{i}")
            return "held", (time.perf_counter() - t0) * 1000, h["reserve"]["attempts"], h["reserve"]["conflicts"], h["shard"]
        except InsufficientStock:
            return "sold_out", (time.perf_counter() - t0) * 1000, 0, 0, None
        except Contention as e:
            return "contention", (time.perf_counter() - t0) * 1000, e.detail["attempts"], e.detail["conflicts"], None

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(one, range(orders)))
    wall = time.perf_counter() - t0
    after = inventory.stock_report(sku)
    held = [r for r in results if r[0] == "held"]
    lat = [r[1] for r in held]
    per_shard: dict[int, int] = {}
    for r in held:
        per_shard[r[4]] = per_shard.get(r[4], 0) + 1
    return {
        "sku": sku, "shards": len(after["shards"]), "orders": orders, "concurrency": concurrency, "qty": qty,
        "held": len(held), "sold_out": sum(r[0] == "sold_out" for r in results),
        "contention": sum(r[0] == "contention" for r in results),
        "conflicts_total": sum(r[3] for r in results), "conflicts_per_hold": round(sum(r[3] for r in held) / max(1, len(held)), 2),
        "max_attempts": max((r[2] for r in results), default=0),
        "latency_ms": {"p50": _pct(lat, .5), "p95": _pct(lat, .95), "p99": _pct(lat, .99), "max": _pct(lat, 1)},
        "wall_s": round(wall, 2), "holds_per_s": round(len(held) / wall, 1) if wall else 0,
        "per_shard": dict(sorted(per_shard.items())),
        "before": before, "after": after, "oversold": after["oversold"], "balanced": after["balanced"],
    }
# @learn:end flash-sale
