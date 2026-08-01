"""Authentication module — email-only login with JWT tokens."""

from app.auth.dependencies import get_current_user
from app.auth.router import router as auth_router

__all__ = ["auth_router", "get_current_user"]
