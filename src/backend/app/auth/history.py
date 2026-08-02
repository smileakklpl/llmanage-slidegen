"""Generation history storage backed by DynamoDB.

Each record represents one completed generation job for a user.
Schema (slidegen_history table):
  PK  = email (HASH)
  SK  = record_id (RANGE) — format: {ISO timestamp}#{job_id}

Attributes:
  - job_id: str
  - prompt: str (the user's original prompt)
  - created_at: str (ISO 8601)
  - artifacts: list[dict] — [{filename, object_key, type, size_bytes}]
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from boto3.dynamodb.conditions import Key

from app.auth.dynamodb import get_history_table
from app.core.logging import get_logger

logger = get_logger(__name__)


def _make_record_id(created_at: str, job_id: str) -> str:
    """Build a sort key that orders by time descending (newest first via reverse scan)."""
    return f"{created_at}#{job_id}"


def add_history_record(
    email: str,
    job_id: str,
    prompt: str,
    artifacts: list[dict[str, Any]],
    created_at: str | None = None,
) -> dict[str, Any]:
    """Add a generation history record for a user.

    Returns the stored item dict.
    """
    if not created_at:
        created_at = datetime.now(timezone.utc).isoformat()

    record_id = _make_record_id(created_at, job_id)

    item = {
        "email": email.strip().lower(),
        "record_id": record_id,
        "job_id": job_id,
        "prompt": prompt,
        "created_at": created_at,
        "artifacts": artifacts,
    }

    table = get_history_table()
    table.put_item(Item=item)
    logger.info("Added history record for %s: job=%s", email, job_id)
    return item


def list_history(email: str, limit: int = 50) -> list[dict[str, Any]]:
    """List generation history for a user, ordered by newest first.

    Uses ScanIndexForward=False for descending sort key order.
    """
    table = get_history_table()
    response = table.query(
        KeyConditionExpression=Key("email").eq(email.strip().lower()),
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])


def get_history_record(email: str, record_id: str) -> dict[str, Any] | None:
    """Get a single history record by email + record_id."""
    table = get_history_table()
    response = table.get_item(
        Key={"email": email.strip().lower(), "record_id": record_id}
    )
    return response.get("Item")


def delete_history_record(email: str, record_id: str) -> bool:
    """Delete a history record. Returns True if successful."""
    table = get_history_table()
    table.delete_item(
        Key={"email": email.strip().lower(), "record_id": record_id}
    )
    logger.info("Deleted history record for %s: %s", email, record_id)
    return True
