"""User registry backed by a JSON file.

Schema of config/users.json:
[
  {"email": "alice@example.com", "name": "Alice"},
  {"email": "bob@example.com", "name": "Bob"}
]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.auth.config import USERS_FILE
from app.core.logging import get_logger

logger = get_logger(__name__)


def _resolve_users_file() -> Path:
    """Resolve the users file path (supports relative and absolute)."""
    path = Path(USERS_FILE)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return path.resolve()


def _load_users() -> list[dict[str, Any]]:
    """Load user list from JSON file. Returns empty list if file not found."""
    path = _resolve_users_file()
    if not path.exists():
        logger.warning("Users file not found: %s — no registered users", path)
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            logger.error("Users file is not a JSON array: %s", path)
            return []
        return data
    except Exception:
        logger.exception("Failed to load users file: %s", path)
        return []


def _save_users(users: list[dict[str, Any]]) -> None:
    """Persist user list to JSON file."""
    path = _resolve_users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    """Lookup a user by email (case-insensitive)."""
    email_lower = email.strip().lower()
    for user in _load_users():
        if user.get("email", "").strip().lower() == email_lower:
            return user
    return None


def register_user(email: str, name: str = "") -> dict[str, Any]:
    """Register a new user. Raises ValueError if email already exists."""
    email = email.strip().lower()
    if not email:
        raise ValueError("Email is required")

    existing = get_user_by_email(email)
    if existing:
        raise ValueError(f"Email already registered: {email}")

    users = _load_users()
    new_user = {"email": email, "name": name or email.split("@")[0]}
    users.append(new_user)
    _save_users(users)
    return new_user


def list_users() -> list[dict[str, Any]]:
    """Return all registered users."""
    return _load_users()
