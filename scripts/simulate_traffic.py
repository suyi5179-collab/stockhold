#!/usr/bin/env python3
"""Synthetic shoppers, so the event log has something to analyse.

Per product, each buyer reserves, then (by a fixed seed) pays after a random wait, has the payment
fail, or walks away and lets the hold expire. Everything goes through the real API path: reserve,
expiry message, payment-events queue and the Lambdas.

    .venv/bin/python scripts/simulate_traffic.py --buyers 120 --hold-seconds 12
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import threading
import time
import uuid

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:45671")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from stockhold import expiry, inventory, payments  # noqa: E402
from stockhold.states import Contention  # noqa: E402

# product: (share of buyers who pay, who fail payment, who walk away; mean seconds to pay)
PROFILES = {"tee": (0.70, 0.10, 0.20, 3.0), "sneaker": (0.45, 0.15, 0.40, 6.0), "mug": (0.85, 0.05, 0.10, 2.0)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--buyers", type=int, default=120)
    ap.add_argument("--hold-seconds", type=int, default=12)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--arrival-seconds", type=float, default=40, help="buyers arrive uniformly over this window")
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    run = uuid.uuid4().hex[:4]
    skus = {}
    for name in PROFILES:
        skus[name] = f"sim-{name}-{run}"
        inventory.create_sku(skus[name], a.buyers, 4 if name == "sneaker" else 1)
    plan = []
    for i in range(a.buyers):
        name = rnd.choice(list(PROFILES))
        pay, fail, _walk, mean = PROFILES[name]
        x = rnd.random()
        action = "pay" if x < pay else "fail" if x < pay + fail else "walk"
        wait = min(a.hold_seconds * 1.5, rnd.expovariate(1 / mean))
        plan.append((i, name, action, wait))

    def buyer(i, name, action, wait):
        time.sleep(rnd.random() * a.arrival_seconds)
        for attempt in range(8):        # a 503 CONTENTION means "retry with the same key", as a client would
            try:
                h = inventory.reserve(skus[name], 1, f"sim-{run}-{i}", hold_seconds=a.hold_seconds,
                                      client="sim", idempotency_key=f"{run}-{i}")
                break
            except Contention:
                retries[0] += 1
                time.sleep(0.2 * (attempt + 1))
        else:
            return
        expiry.schedule(h)
        if action == "walk":
            return
        time.sleep(wait)
        payments.publish("payment_succeeded" if action == "pay" else "payment_failed", h["hold_id"],
                         extra={"order_id": h["order_id"], "sku": h["sku"], "qty": 1})

    retries = [0]
    ts = [threading.Thread(target=buyer, args=p) for p in plan]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print(f"run {run}: {a.buyers} buyers over {list(skus.values())}; waiting for holds to settle")
    deadline = time.time() + a.hold_seconds + 60
    while time.time() < deadline:
        open_ = sum(inventory.stock_report(s)["held"] for s in skus.values())
        if open_ == 0:
            break
        time.sleep(2)
    for s in skus.values():
        r = inventory.stock_report(s)
        print(f"{s}: confirmed={r['confirmed']} released={r['released']} expired={r['expired']} held={r['held']} oversold={r['oversold']}")
    print(f"client retries after 503 CONTENTION: {retries[0]}")
    print(f"sku prefix: sim-*-{run}")


if __name__ == "__main__":
    main()
