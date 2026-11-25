"""Prometheus metrics (CloudWatch metrics + alarms in AWS). Scraped from /metrics on the Flask app.

Counters and histograms are updated in-process by the API; gauges about queues, the DLQ archive and
stock invariants are computed at scrape time from the real stores, cached for a few seconds.
"""
from __future__ import annotations

import threading
import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily, REGISTRY

RESERVE = Counter("stockhold_reservations_total", "Reserve calls by outcome", ["outcome"])
TRANSITIONS = Counter("stockhold_transitions_total", "Hold transitions made through the API", ["to"])
CONFLICTS = Histogram("stockhold_reserve_conflicts", "Optimistic-concurrency conflicts per reserve call",
                      buckets=[0, 1, 2, 3, 5, 8, 12])
LATENCY = Histogram("stockhold_reserve_seconds", "Reserve latency", buckets=[.01, .025, .05, .1, .25, .5, 1, 2.5, 5])
REPLAYS = Counter("stockhold_replays_total", "DLQ replays by result", ["result"])


class StoreCollector:
    """Gauges read from SQS and DynamoDB at scrape time (cached 5 s so a fast scrape is cheap)."""

    def __init__(self):
        self._cache, self._at, self._lock = [], 0.0, threading.Lock()

    def collect(self):
        with self._lock:
            if time.time() - self._at > 5:
                try:
                    self._cache = list(self._read())
                except Exception:  # noqa: BLE001 - a down LocalStack must not break /metrics
                    self._cache = [GaugeMetricFamily("stockhold_store_up", "Stores reachable", value=0)]
                self._at = time.time()
            yield from self._cache

    def _read(self):
        from . import dlq, expiry, inventory, seed
        from .aws import queue_url, sqs
        from .config import settings
        yield GaugeMetricFamily("stockhold_store_up", "Stores reachable", value=1)
        depth = GaugeMetricFamily("stockhold_queue_messages", "SQS messages by queue and state", labels=["queue", "state"])
        s = settings()
        for q in (s.payments_queue, s.payments_dlq, s.expiry_queue):
            a = sqs().get_queue_attributes(QueueUrl=queue_url(q), AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "ApproximateNumberOfMessagesDelayed"])["Attributes"]
            depth.add_metric([q, "visible"], float(a["ApproximateNumberOfMessages"]))
            depth.add_metric([q, "in_flight"], float(a["ApproximateNumberOfMessagesNotVisible"]))
            depth.add_metric([q, "delayed"], float(a.get("ApproximateNumberOfMessagesDelayed", 0)))
        yield depth
        al = dlq.alerts()
        yield GaugeMetricFamily("stockhold_dlq_open", "Archived dead letters not yet replayed", value=al["open"])
        yield GaugeMetricFamily("stockhold_dlq_oldest_open_age_seconds", "Age of the oldest unhandled dead letter",
                                value=al["oldest_open_age_s"])
        yield GaugeMetricFamily("stockhold_dlq_redriven_15m", "Messages dead-lettered in the last 15 minutes",
                                value=al["redriven_15m"])
        yield GaugeMetricFamily("stockhold_holds_overdue", "HELD holds more than the grace period past expiresAt",
                                value=len(expiry.overdue()))
        over = GaugeMetricFamily("stockhold_oversold_units", "Units held or sold beyond capacity (must stay 0)", labels=["sku"])
        neg = GaugeMetricFamily("stockhold_available_units", "Units available to sell", labels=["sku"])
        for sku, *_ in seed.DEMO_SKUS:
            try:
                r = inventory.stock_report(sku)
            except Exception:  # noqa: BLE001
                continue
            over.add_metric([sku], r["oversold"] + len(r["negative_shards"]))
            neg.add_metric([sku], r["available"])
        yield over
        yield neg


_registered = False


def register() -> None:
    global _registered
    if not _registered:
        REGISTRY.register(StoreCollector())
        _registered = True


def exposition() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
