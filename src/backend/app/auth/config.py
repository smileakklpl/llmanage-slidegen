"""Auth-specific configuration."""

import os
import secrets

# JWT settings — override via environment variables
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # default: 24h

# Path to user registry JSON file
USERS_FILE: str = os.getenv(
    "AUTH_USERS_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "config", "users.json"),
)
