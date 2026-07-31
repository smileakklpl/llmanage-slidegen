"""Versioned JSON contracts for one end-to-end deck generation job."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CONTRACT_VERSION = "1.0"


class StoredObjectRef(BaseModel):
    """Reference to an object persisted in S3."""

    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    etag: str | None = None


class GenerationRequest(BaseModel):
    """Deterministic input contract for the callable generation pipeline.

    ``input_path`` and ``output_dir`` are worker-local temporary paths. The
    backend-facing contract stores the durable S3 references in ``JobModel``;
    local paths never become the persisted source of truth.
    """

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    job_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    input_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    sections: list[str] | None = None
    deck_title: str | None = None
    use_fake_llm: bool = False
    skip_semantic_review: bool = False
    generation_policy: Literal["strict", "required"] | None = None
    generation_deadline_seconds: float | None = Field(default=None, gt=0)
    generation_render_reserve_seconds: float | None = Field(default=None, ge=0)


class GeneratedArtifact(BaseModel):
    """A locally generated artifact before the worker persists it to S3."""

    artifact_type: Literal["pptx", "xlsx", "json"]
    filename: str
    path: str
    media_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GenerationResult(BaseModel):
    """Structured result returned by the callable orchestrator."""

    contract_version: Literal["1.0"] = CONTRACT_VERSION
    job_id: str
    status: Literal["succeeded"] = "succeeded"
    artifacts: list[GeneratedArtifact]
    verification_passed: bool
    series_checked: int = Field(ge=0)
    external_checked: int = Field(ge=0)
    page_count: int = Field(ge=0)
    slide_count: int = Field(ge=0)
    chart_count: int = Field(ge=0)
