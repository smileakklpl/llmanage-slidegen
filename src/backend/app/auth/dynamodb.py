"""DynamoDB client and table initialization for auth module.

Provides a shared DynamoDB resource and ensures required tables exist
(creates them on first access if using DynamoDB Local for development).
"""

from __future__ import annotations

import boto3
from botocore.exceptions import ClientError

from app.auth.config import (
    AWS_REGION,
    DYNAMODB_ENDPOINT_URL,
    DYNAMODB_HISTORY_TABLE,
    DYNAMODB_USERS_TABLE,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_resource = None


def get_dynamodb_resource():
    """Return a shared boto3 DynamoDB resource (lazy singleton)."""
    global _resource
    if _resource is None:
        kwargs: dict = {"region_name": AWS_REGION}
        if DYNAMODB_ENDPOINT_URL:
            kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL
        _resource = boto3.resource("dynamodb", **kwargs)
        logger.info(
            "DynamoDB resource initialized (endpoint=%s, region=%s)",
            DYNAMODB_ENDPOINT_URL or "AWS default",
            AWS_REGION,
        )
    return _resource


def get_users_table():
    """Return the Users table object, creating it if it doesn't exist."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_USERS_TABLE)
    try:
        table.load()
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            table = _create_users_table(dynamodb)
        else:
            raise
    return table


def get_history_table():
    """Return the History table object, creating it if it doesn't exist."""
    dynamodb = get_dynamodb_resource()
    table = dynamodb.Table(DYNAMODB_HISTORY_TABLE)
    try:
        table.load()
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            table = _create_history_table(dynamodb)
        else:
            raise
    return table


def _create_users_table(dynamodb):
    """Create the users table (used in development with DynamoDB Local)."""
    logger.info("Creating DynamoDB table: %s", DYNAMODB_USERS_TABLE)
    table = dynamodb.create_table(
        TableName=DYNAMODB_USERS_TABLE,
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    logger.info("Created table: %s", DYNAMODB_USERS_TABLE)
    return table


def _create_history_table(dynamodb):
    """Create the history table (used in development with DynamoDB Local).

    Schema:
      PK = email (HASH)
      SK = record_id (RANGE) — format: {ISO timestamp}#{job_id}
    """
    logger.info("Creating DynamoDB table: %s", DYNAMODB_HISTORY_TABLE)
    table = dynamodb.create_table(
        TableName=DYNAMODB_HISTORY_TABLE,
        KeySchema=[
            {"AttributeName": "email", "KeyType": "HASH"},
            {"AttributeName": "record_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "email", "AttributeType": "S"},
            {"AttributeName": "record_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    logger.info("Created table: %s", DYNAMODB_HISTORY_TABLE)
    return table
