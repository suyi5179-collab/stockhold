"""Hold event log: DynamoDB Streams -> Lambda -> S3 as JSON Lines, partitioned by date and hour.

Athena would query this in AWS; here DuckDB runs the same SQL (sql/*.sql, scripts/analyze.py).
"""
from __future__ import annotations

import datetime as dt
import json
import os

from boto3.dynamodb.types import TypeDeserializer

from .aws import s3
from .dynamo import _plain

BUCKET = os.environ.get("STOCKHOLD_EVENTS_BUCKET", "stockhold-events")
PREFIX = "events/"
_de = TypeDeserializer()


def _img(rec: dict, which: str) -> dict | None:
    raw = rec.get("dynamodb", {}).get(which)
    return {k: _plain(_de.deserialize(v)) for k, v in raw.items()} if raw else None


# @learn:stream-to-event
def to_event(rec: dict) -> dict | None:
    """One stream record -> one hold event, or None if it is not a hold state change."""
    new, old = _img(rec, "NewImage"), _img(rec, "OldImage")
    if not new or not str(new.get("PK", "")).startswith("HOLD#"):
        return None
    before = old.get("status") if old else None
    if before == new["status"]:
        return None                       # e.g. the expiry message id being recorded
    last = new["history"][-1]
    ev = {
        "event_id": rec.get("eventID"), "hold_id": new["hold_id"], "sku": new["sku"], "shard": new["shard"],
        "qty": new["qty"], "order_id": new["order_id"], "from_status": before, "to_status": new["status"],
        "at_ms": last["at_ms"], "actor": last.get("by"), "created_at_ms": new["created_at_ms"],
        "expires_at_ms": new["expires_at_ms"], "hold_seconds": new["hold_seconds"],
        "wait_ms": last["at_ms"] - new["created_at_ms"],
        "lag_after_expiry_ms": last["at_ms"] - new["expires_at_ms"] if new["status"] == "EXPIRED" else None,
    }
    return ev
# @learn:end stream-to-event


def key_for(at_ms: int, name: str) -> str:
    t = dt.datetime.fromtimestamp(at_ms / 1000, dt.timezone.utc)
    return f"{PREFIX}dt={t:%Y-%m-%d}/hour={t:%H}/{name}.jsonl"


def write(events: list[dict], name: str) -> list[str]:
    """Write events as JSON Lines, one object per (date, hour) partition. Returns the keys."""
    parts: dict[str, list[dict]] = {}
    for e in events:
        parts.setdefault(key_for(e["at_ms"], name), []).append(e)
    for key, evs in parts.items():
        s3().put_object(Bucket=BUCKET, Key=key, ContentType="application/x-ndjson",
                        Body=("\n".join(json.dumps(e, sort_keys=True) for e in evs) + "\n").encode())
    return sorted(parts)


def download(dest: str, prefix: str = PREFIX) -> int:
    """Mirror the event log to a local folder (keeps the dt=/hour= partitions). Returns files fetched."""
    n, kw = 0, {}
    while True:
        r = s3().list_objects_v2(Bucket=BUCKET, Prefix=prefix, **kw)
        for o in r.get("Contents", []):
            path = os.path.join(dest, o["Key"])
            if os.path.exists(path) and os.path.getsize(path) == o["Size"]:
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            s3().download_file(BUCKET, o["Key"], path)
            n += 1
        if not r.get("IsTruncated"):
            return n
        kw = {"ContinuationToken": r["NextContinuationToken"]}


def find(hold_id: str, day_ms: int) -> list[dict]:
    """The S3 objects (and lines) holding one hold's events, searched in that day's partition."""
    t = dt.datetime.fromtimestamp(day_ms / 1000, dt.timezone.utc)
    out = []
    r = s3().list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}dt={t:%Y-%m-%d}/")
    for o in sorted(r.get("Contents", []), key=lambda o: o["Key"], reverse=True)[:200]:
        body = s3().get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read().decode()
        for line in body.splitlines():
            if hold_id in line:
                out.append({"key": o["Key"], "event": json.loads(line)})
    return out
