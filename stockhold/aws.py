"""boto3 clients pointed at LocalStack (AWS_ENDPOINT_URL); inside a Lambda, LocalStack injects the endpoint."""
from __future__ import annotations

import functools
import os

import boto3
from botocore.config import Config

from .config import settings


@functools.lru_cache(maxsize=None)
def _client(service: str, endpoint: str, region: str):
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    return boto3.client(
        service, endpoint_url=endpoint, region_name=region,
        # No SDK-level retries on DynamoDB: conflicts are retried by our own loop, so they can be counted.
        config=Config(retries={"max_attempts": 1 if service == "dynamodb" else 3, "mode": "standard"},
                      max_pool_connections=64, s3={"addressing_style": "path"}),
    )


def client(service: str):
    s = settings()
    return _client(service, s.aws_endpoint, s.aws_region)


def ddb():
    return client("dynamodb")


def sqs():
    return client("sqs")


def s3():
    return client("s3")
