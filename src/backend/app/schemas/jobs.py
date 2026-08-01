"""Pydantic v2 models for job-related API responses.

These models match contracts/job-status.schema.json.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(StrEnum):
    """High-level status of a job."""

    queued = "queued"
    running = "running"
    waiting_review = "waiting_review"
    succeeded = "succeeded"
    failed = "failed"


class JobStage(StrEnum):
    """Processing stage of a job."""

    queued = "queued"
    parsing_intent = "parsing_intent"
    analyzing_data = "analyzing_data"
    reviewing_data = "reviewing_data"
    writing_insights = "writing_insights"
    rendering = "rendering"
    validating = "validating"
    completed = "completed"
    failed = "failed"


class Artifact(BaseModel):
    """An output artifact produced by a completed job and persisted in S3."""

    type: str
    filename: str
    download_url: str
    object_key: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int | None = Field(default=None, ge=0)


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
    review_required_count: int = Field(default=0, ge=0)
    review_url: str | None = None


class JobCreateResponse(BaseModel):
    """Response for POST /api/v1/jobs/generate (HTTP 202)."""

    job_id: str
    status: str = "queued"
    status_url: str


class ReviewSource(BaseModel):
    filename: str
    preview_url: str


class JobReviewResponse(BaseModel):
    job_id: str
    review_required_count: int = Field(ge=0)
    can_resume: bool
    datasets: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[ReviewSource] = Field(default_factory=list)


class ResumeJobResponse(BaseModel):
    job_id: str
    status: JobStatus
    stage: JobStage
    message: str


class SendEmailResponse(BaseModel):
    """Response for POST /api/v1/jobs/{job_id}/send."""

    job_id: str
    sender: str
    recipients: list[str]
    subject: str
    attachment_count: int
    message: str
