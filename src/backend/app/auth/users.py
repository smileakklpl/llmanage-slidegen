"""User registry backed by DynamoDB with bcrypt password hashing.

Schema (slidegen_users table):
  PK = email (HASH)

Attributes:
  - email: str
  - name: str
  - password_hash: str (bcrypt)
  - created_at: str (ISO 8601)
  - is_active: bool
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import bcrypt
from boto3.dynamodb.conditions import Attr

from app.auth.dynamodb import get_users_table
from app.core.logging import get_logger

logger = get_logger(__name__)


def _hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Lookup a user by email (case-insensitive). Returns None if not found."""
    table = get_users_table()
    response = table.get_item(Key={"email": email.strip().lower()})
    return response.get("Item")


def verify_user_password(email: str, password: str) -> dict[str, Any] | None:
    """Verify email + password. Returns user dict if valid, None otherwise."""
    user = get_user_by_email(email)
    if not user:
        return None

    if not user.get("is_active", True):
        return None

    password_hash = user.get("password_hash", "")
    if not password_hash:
        return None

    if _verify_password(password, password_hash):
        return user
    return None


def register_user(email: str, password: str, name: str = "") -> dict[str, Any]:
    """Register a new user. Raises ValueError if email already exists."""
    email = email.strip().lower()
    if not email:
        raise ValueError("Email is required")
    if not password:
        raise ValueError("Password is required")

    existing = get_user_by_email(email)
    if existing:
        raise ValueError(f"Email already registered: {email}")

    item = {
        "email": email,
        "name": name or email.split("@")[0],
        "password_hash": _hash_password(password),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
    }

    table = get_users_table()
    table.put_item(
        Item=item,
        ConditionExpression=Attr("email").not_exists(),
    )
    logger.info("Registered new user: %s", email)
    return {"email": item["email"], "name": item["name"]}


def list_users() -> list[dict[str, Any]]:
    """Return all registered users (without password hashes)."""
    table = get_users_table()
    response = table.scan(
        ProjectionExpression="email, #n, created_at, is_active",
        ExpressionAttributeNames={"#n": "name"},
    )
    return response.get("Items", [])


def update_user(email: str, **fields) -> dict[str, Any] | None:
    """Update user fields (name, is_active). Returns updated user or None."""
    email = email.strip().lower()
    user = get_user_by_email(email)
    if not user:
        return None

    table = get_users_table()
    update_parts = []
    attr_names = {}
    attr_values = {}

    if "name" in fields:
        update_parts.append("#n = :name")
        attr_names["#n"] = "name"
        attr_values[":name"] = fields["name"]

    if "is_active" in fields:
        update_parts.append("is_active = :active")
        attr_values[":active"] = fields["is_active"]

    if "password" in fields:
        update_parts.append("password_hash = :ph")
        attr_values[":ph"] = _hash_password(fields["password"])

    if not update_parts:
        return user

    table.update_item(
        Key={"email": email},
        UpdateExpression="SET " + ", ".join(update_parts),
        ExpressionAttributeNames=attr_names or None,
        ExpressionAttributeValues=attr_values,
    )
    return get_user_by_email(email)
