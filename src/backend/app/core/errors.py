"""Global error handling infrastructure."""

import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom exception classes
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base application error with a machine-readable code and human message."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    """Resource not found."""

    def __init__(self, code: str = "NOT_FOUND", message: str = "找不到指定的資源") -> None:
        super().__init__(code=code, message=message, status_code=404)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _build_error_response(code: str, message: str, status_code: int) -> JSONResponse:
    """Build a consistent error JSON response."""
    request_id = str(uuid.uuid4())
    return JSONResponse(
        status_code=status_code,
        content={
            "code": code,
            "message": message,
            "request_id": request_id,
        },
    )


# ---------------------------------------------------------------------------
# FastAPI exception handlers
# ---------------------------------------------------------------------------


async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    """Handle expected application errors."""
    logger.warning("AppError: code=%s message=%s", exc.code, exc.message)
    return _build_error_response(exc.code, exc.message, exc.status_code)


# ---------------------------------------------------------------------------
# Middleware for catching unexpected exceptions
# ---------------------------------------------------------------------------


class CatchAllExceptionMiddleware(BaseHTTPMiddleware):
    """Catch any unhandled exception and return a safe 500 JSON response."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error: %s", exc)
            return _build_error_response(
                code="INTERNAL_ERROR",
                message="伺服器發生非預期錯誤",
                status_code=500,
            )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_error_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI application."""
    # Middleware catches truly unexpected errors (non-AppError).
    app.add_middleware(CatchAllExceptionMiddleware)
    # Exception handler catches AppError subtypes raised in route handlers.
    app.add_exception_handler(AppError, _handle_app_error)  # type: ignore[arg-type]
