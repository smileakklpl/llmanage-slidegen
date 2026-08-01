"""JWT token creation and verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.auth.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET_KEY


def create_access_token(data: dict[str, Any]) -> str:
    """Create a JWT access token with expiration."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT access token.

    Raises jwt.InvalidTokenError on failure.
    """
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
