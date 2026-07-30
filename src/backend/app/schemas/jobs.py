"""Pydantic v2 models for job-related API responses.

These models match contracts/job-status.schema.json.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """High-level status of a job."""

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class JobStage(StrEnum):
    """Processing stage of a job."""

    queued = "queued"
    parsing_intent = "parsing_intent"
    analyzing_data = "analyzing_data"
    writing_insights = "writing_insights"
    rendering = "rendering"
    validating = "validating"
    completed = "completed"
    failed = "failed"


class Artifact(BaseModel):
    """An output artifact produced by a completed job."""

    type: str
    filename: str
    download_url: str


class JobError(BaseModel):
    """Error details when a job fails."""

    code: str
    message: str


class JobStatusResponse(BaseModel):
    """Full job status response (GET /api/v1/jobs/{job_id})."""

    job_id: str
    status: JobStatus
    stage: JobStage
    progress: int = Field(ge=0, le=100)
    message: str
    created_at: datetime
    updated_at: datetime
    artifacts: list[Artifact] = Field(default_factory=list)
    error: JobError | None = None
    summary: str | None = None


class JobCreateResponse(BaseModel):
    """Response for POST /api/v1/jobs/generate (HTTP 202)."""

    job_id: str
    status: str = "queued"
    status_url: str


class SendEmailResponse(BaseModel):
    """Response for POST /api/v1/jobs/{job_id}/send."""

    job_id: str
    sender: str
    recipients: list[str]
    subject: str
    attachment_count: int
    message: str
