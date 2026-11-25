#!/usr/bin/env python3
"""Measure the eventual-release window: how long after expiresAt a hold really becomes EXPIRED.

    .venv/bin/python scripts/measure_expiry_lag.py --holds 30 --hold-seconds 3
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:45671")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from stockhold import expiry, inventory  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holds", type=int, default=30)
    ap.add_argument("--hold-seconds", type=int, default=3)
    a = ap.parse_args()
    sku = f"lag-{uuid.uuid4().hex[:8]}"
    inventory.create_sku(sku, a.holds, 1)
    ids = []
    for i in range(a.holds):
        h = inventory.reserve(sku, 1, f"o-{i}", hold_seconds=a.hold_seconds)
        expiry.schedule(h)
        ids.append(h["hold_id"])
    deadline = time.time() + a.hold_seconds + 90
    lags = {}
    while len(lags) < len(ids) and time.time() < deadline:
        for hid in ids:
            if hid in lags:
                continue
            h = inventory.get_hold(hid)
            if h["status"] == "EXPIRED":
                lags[hid] = (h["history"][-1]["at_ms"] - h["expires_at_ms"], h["history"][-1]["by"])
        time.sleep(0.5)
    ms = sorted(v[0] for v in lags.values())
    by = {}
    for _, b in lags.values():
        by[b] = by.get(b, 0) + 1
    q = lambda p: ms[min(len(ms) - 1, int(p * len(ms)))]  # noqa: E731
    print(f"holds={a.holds} hold_seconds={a.hold_seconds} expired={len(ms)} by={by}")
    print(f"lag after expiresAt ms: min={ms[0]} p50={q(.5)} p90={q(.9)} max={ms[-1]} mean={statistics.mean(ms):.0f}")


if __name__ == "__main__":
    main()
