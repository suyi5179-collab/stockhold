"""Fault injection for the payment consumer: 'the next N attempts for this hold fail'.

Stored in DynamoDB so the deployed Lambda sees it without a redeploy. Counting down (instead of a
time window) makes the demo deterministic: with maxReceiveCount=3, three failures put the message in
the DLQ, and a replay afterwards succeeds.
"""
from __future__ import annotations

from botocore.exceptions import ClientError

from .aws import ddb
from .config import settings
from .dynamo import error_code, item, plain, vals


def _key(target: str) -> dict:
    return item({"PK": f"FAULT#{target}", "SK": "FAULT"})


def arm(target: str, failures: int) -> None:
    """target is a hold id. (No wildcard on purpose: a global fault would also hit other people's demos.)"""
    ddb().put_item(TableName=settings().table, Item=item({"PK": f"FAULT#{target}", "SK": "FAULT", "target": target,
                                                           "remaining": failures}))


def clear(target: str) -> None:
    ddb().delete_item(TableName=settings().table, Key=_key(target))


def get(target: str) -> dict | None:
    return plain(ddb().get_item(TableName=settings().table, Key=_key(target), ConsistentRead=True).get("Item"))


def fire(hold_id: str) -> bool:
    """Consume one injected failure for this hold. True means: fail this attempt."""
    try:
        ddb().update_item(TableName=settings().table, Key=_key(hold_id),
                          UpdateExpression="SET remaining = remaining - :one",
                          ConditionExpression="remaining > :zero",
                          ExpressionAttributeValues=vals({":one": 1, ":zero": 0}))
        return True
    except ClientError as e:
        if error_code(e) != "ConditionalCheckFailedException":
            raise
    return False
