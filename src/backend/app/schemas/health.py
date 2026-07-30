"""Response schema for the health endpoint."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Schema for GET /api/v1/health response."""

    status: str
    service: str
