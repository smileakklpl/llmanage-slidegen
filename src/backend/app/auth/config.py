"""Auth-specific configuration."""

import os
import secrets

# JWT settings — override via environment variables
JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", secrets.token_urlsafe(32))
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))  # default: 24h

# DynamoDB settings
DYNAMODB_ENDPOINT_URL: str | None = os.getenv("DYNAMODB_ENDPOINT_URL", None)
DYNAMODB_USERS_TABLE: str = os.getenv("DYNAMODB_USERS_TABLE", "slidegen_users")
DYNAMODB_HISTORY_TABLE: str = os.getenv("DYNAMODB_HISTORY_TABLE", "slidegen_history")
AWS_REGION: str = os.getenv("AWS_REGION", "us-west-2")
