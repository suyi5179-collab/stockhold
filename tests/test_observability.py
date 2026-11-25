"""Metrics, alert inputs and traces: what an on-call engineer would look at."""
import json
import time
import urllib.request
import uuid

import pytest

from stockhold import inventory, payments, tracing


def test_metrics_endpoint_counts_reservations_and_reads_the_stores(api, new_sku):
    sku = new_sku(3)
    r = api.post("/v1/reservations", json={"sku": sku, "qty": 1, "order_id": "o"},
                 headers=api.auth(**{"Idempotency-Key": uuid.uuid4().hex}))
    assert r.status_code == 201
    text = api.get("/metrics").data.decode()
    assert 'stockhold_reservations_total{outcome="held"}' in text
    assert "stockhold_reserve_conflicts_bucket" in text and "stockhold_store_up 1.0" in text
    assert 'stockhold_queue_messages{queue="stockhold-payment-events-dlq",state="visible"}' in text
    for line in text.splitlines():
        if line.startswith("stockhold_oversold_units{"):
            assert line.endswith(" 0.0"), line


def test_hand_written_emitter_nests_spans_and_continues_a_traceparent(monkeypatch):
    """The Lambda path (no OpenTelemetry SDK): spans nest, and a message's traceparent is the parent."""
    sent = []
    monkeypatch.setattr(tracing, "_HAVE_SDK", False)
    monkeypatch.setattr(tracing, "_emit", lambda spans: sent.append(spans))
    parent = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"
    with tracing.span("outer", parent=parent, hold_id="h1") as sp:
        assert tracing.traceparent().startswith("00-" + "a" * 32 + "-")
        with tracing.span("inner"):
            pass
        sp["changed"] = True
    assert len(sent) == 1                                   # one export per root span
    inner, outer = sent[0]
    assert outer["traceId"] == inner["traceId"] == "a" * 32
    assert outer["parentSpanId"] == "b" * 16 and inner["parentSpanId"] == outer["spanId"]
    assert {"key": "changed", "value": {"boolValue": True}} in outer["attributes"]


def _jaeger(path):
    with urllib.request.urlopen("http://localhost:16691" + path, timeout=2) as r:
        return json.loads(r.read())


# @learn:test-trace-e2e
def test_one_trace_from_api_through_the_queue_into_the_lambda(api, new_sku):
    try:
        _jaeger("/api/services")
    except OSError:
        pytest.skip("Jaeger not running (docker-compose -p stockhold up)")
    sku = new_sku(3)
    h = inventory.reserve(sku, 1, "o-trace")
    with tracing.span("test.webhook", hold_id=h["hold_id"]):
        trace_id = tracing.current_trace_id()
        payments.publish("payment_failed", h["hold_id"])
    tracing._otel.get_tracer_provider().force_flush()
    services, t0 = set(), time.time()
    while time.time() - t0 < 40 and services != {"stockhold-api", "stockhold-payments"}:
        time.sleep(1)
        try:
            d = _jaeger(f"/api/traces/{trace_id}")["data"][0]
            services = {d["processes"][s["processID"]]["serviceName"] for s in d["spans"]}
        except (OSError, KeyError, IndexError):
            pass
    assert services == {"stockhold-api", "stockhold-payments"}, services
# @learn:end test-trace-e2e
