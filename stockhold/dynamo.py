"""Small helpers around the low-level DynamoDB client: (de)serialisation and error codes."""
from __future__ import annotations

from decimal import Decimal

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

_ser, _de = TypeSerializer(), TypeDeserializer()


def _plain(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def _dec(v):
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        return {k: _dec(x) for k, x in v.items()}
    return v


def item(d: dict) -> dict:
    return {k: _ser.serialize(_dec(v)) for k, v in d.items() if v is not None}


def vals(d: dict) -> dict:
    return {k: _ser.serialize(_dec(v)) for k, v in d.items()}


def plain(raw: dict | None) -> dict | None:
    if raw is None:
        return None
    return {k: _plain(_de.deserialize(v)) for k, v in raw.items()}


def error_code(e: ClientError) -> str:
    return e.response.get("Error", {}).get("Code", "")


def cancellation_codes(e: ClientError) -> list[str]:
    """For a cancelled TransactWriteItems: one code per action ('None' when that action was fine)."""
    return [r.get("Code", "None") for r in e.response.get("CancellationReasons", [])]
