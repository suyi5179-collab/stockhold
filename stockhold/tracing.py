"""Tracing that works in both places the core code runs.

* In the Flask app the OpenTelemetry SDK is installed: spans go to Jaeger over OTLP/HTTP.
* Inside a Lambda only the standard library and boto3 exist, so a ~40-line emitter writes the same
  OTLP/HTTP JSON by hand. Either way a span started from an SQS message continues the trace whose
  W3C `traceparent` the sender put in the message attributes: API -> queue -> Lambda is one trace.

X-Ray in AWS; here Jaeger (docs/design.md, the substitution table).
"""
from __future__ import annotations

import contextlib
import json
import os
import secrets
import threading
import time
import urllib.request

ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4328")
SERVICE = os.environ.get("OTEL_SERVICE_NAME", "stockhold-api")
ENABLED = os.environ.get("STOCKHOLD_TRACING", "1") == "1"

try:  # the Flask app
    from opentelemetry import trace as _otel
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    _HAVE_SDK = True
except ImportError:  # inside a Lambda
    _HAVE_SDK = False

_local = threading.local()


def setup_sdk() -> None:
    """Called once by the Flask app factory."""
    if not (_HAVE_SDK and ENABLED):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    if isinstance(_otel.get_tracer_provider(), TracerProvider):
        return
    tp = TracerProvider(resource=Resource.create({"service.name": SERVICE}))
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{ENDPOINT}/v1/traces", timeout=2)))
    _otel.set_tracer_provider(tp)


# ------------------------------------------------------------------ the hand-written emitter (Lambda)

def _emit(spans: list[dict]) -> None:
    if not spans:
        return
    payload = {"resourceSpans": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": SERVICE}}]},
                                  "scopeSpans": [{"scope": {"name": "stockhold"}, "spans": spans}]}]}
    req = urllib.request.Request(f"{ENDPOINT}/v1/traces", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=1).read()
    except Exception:  # noqa: BLE001 - tracing must never break the handler
        pass


def _attr(k, v):
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}
    return {"key": k, "value": {"stringValue": str(v)}}


@contextlib.contextmanager
def _mini_span(name: str, parent: str | None, attrs: dict):
    stack = getattr(_local, "stack", None) or []
    _local.stack = stack
    if stack:
        trace_id, parent_id = stack[-1]["traceId"], stack[-1]["spanId"]
    elif parent and len(parent.split("-")) == 4:
        _, trace_id, parent_id, _ = parent.split("-")
    else:
        trace_id, parent_id = secrets.token_hex(16), ""
    span = {"traceId": trace_id, "spanId": secrets.token_hex(8), "parentSpanId": parent_id, "name": name,
            "kind": 2, "startTimeUnixNano": str(time.time_ns()), "attributes": [], "status": {}}
    stack.append(span)
    holder = {}
    try:
        yield holder
    except Exception as e:
        span["status"] = {"code": 2, "message": repr(e)[:200]}
        raise
    finally:
        stack.pop()
        span["endTimeUnixNano"] = str(time.time_ns())
        span["attributes"] = [_attr(k, v) for k, v in {**attrs, **holder}.items() if v is not None]
        _local.done = getattr(_local, "done", []) + [span]
        if not stack:               # root finished: ship the whole trace in one request
            _emit(_local.done)
            _local.done = []


# ------------------------------------------------------------------ one interface for the core code

@contextlib.contextmanager
def span(name: str, parent: str | None = None, **attrs):
    """`with span("reserve", sku=...) as s: s["attempts"] = 3` - attributes may be added inside."""
    if not ENABLED:
        yield {}
        return
    if not _HAVE_SDK:
        with _mini_span(name, parent, attrs) as h:
            yield h
        return
    tracer = _otel.get_tracer("stockhold")
    ctx = TraceContextTextMapPropagator().extract({"traceparent": parent}) if parent else None
    with tracer.start_as_current_span(name, context=ctx) as sp:
        holder: dict = {}
        for k, v in attrs.items():
            if v is not None:
                sp.set_attribute(k, v)
        try:
            yield holder
        finally:
            for k, v in holder.items():
                if v is not None:
                    sp.set_attribute(k, v)


def traceparent() -> str | None:
    """The current span as a W3C traceparent, to put on an outgoing SQS message."""
    if not ENABLED:
        return None
    if not _HAVE_SDK:
        stack = getattr(_local, "stack", None)
        return f"00-{stack[-1]['traceId']}-{stack[-1]['spanId']}-01" if stack else None
    carrier: dict = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def current_trace_id() -> str | None:
    tp = traceparent()
    return tp.split("-")[1] if tp else None
