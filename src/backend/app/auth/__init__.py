"""Authentication module — email + password login with JWT tokens, DynamoDB backend."""

from app.auth.dependencies import get_current_user
from app.auth.history_router import router as history_router
from app.auth.router import router as auth_router

__all__ = ["auth_router", "history_router", "get_current_user"]
