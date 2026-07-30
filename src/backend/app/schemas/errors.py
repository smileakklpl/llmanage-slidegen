"""Pydantic v2 model for standardized error responses."""

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Unified error response format for all API errors."""

    code: str
    message: str
    request_id: str
