"""Versioned JSON contracts for one end-to-end deck generation job."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CONTRACT_VERSION = "1.1"


class StoredObjectRef(BaseModel):
    """Reference to an object persisted in S3."""

    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    etag: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class GenerationOptions(BaseModel):
    """Durable delivery policy captured when a job is created."""

    policy: Literal["strict", "required"] = "required"
    deadline_seconds: float = Field(default=900.0, gt=0)
    render_reserve_seconds: float = Field(default=180.0, ge=0)
    use_fake_llm: bool = False
    skip_semantic_review: bool = False

    @model_validator(mode="after")
    def validate_reserve(self) -> GenerationOptions:
        if self.render_reserve_seconds >= self.deadline_seconds:
            raise ValueError("render_reserve_seconds 必須小於 deadline_seconds")
        return self


class NormalizedColumnContract(BaseModel):
    """Column metadata required by the deterministic generation engine."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    index: int = Field(ge=0)
    data_type: Literal[
        "string", "integer", "number", "date", "datetime", "boolean", "mixed", "empty"
    ]
    unit: str | None = None


class NormalizedValueContract(BaseModel):
    """One normalized cell value plus backend-owned provenance evidence."""

    model_config = ConfigDict(extra="allow")

    value: Any = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class NormalizedRecordContract(BaseModel):
    """One normalized dataset record keyed by column key."""

    model_config = ConfigDict(extra="allow")

    record_index: int = Field(ge=0)
    values: dict[str, NormalizedValueContract]


class NormalizedDatasetContract(BaseModel):
    """Validated dataset shape accepted by deterministic metric calculation."""

    model_config = ConfigDict(extra="allow")

    dataset_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    source_kind: str = Field(min_length=1)
    table_kind: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    columns: list[NormalizedColumnContract] = Field(min_length=1)
    records: list[NormalizedRecordContract] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_review: bool = False
    review_status: str = "not_required"
    warnings: list[str] = Field(default_factory=list)


class NormalizedIngestionContract(BaseModel):
    """Versioned JSON boundary from backend ingestion into core generation."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    filename: str = Field(min_length=1)
    pipeline_status: Literal[
        "completed",
        "completed_with_warnings",
        "unsupported",
        "rejected",
        "failed",
    ]
    source_files: list[str] = Field(min_length=1)
    datasets: list[NormalizedDatasetContract] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class GenerationRequest(BaseModel):
    """JSON boundary from core to the normalized generation pipeline.

    ``ingestion_path`` and ``output_dir`` are worker-local materializations.
    Durable S3 provenance remains in ``source_objects`` and the backend job.
    Raw Excel never crosses from core into ppt_generation.
    """

    contract_version: Literal["1.1"] = CONTRACT_VERSION
    job_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    ingestion_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    source_objects: list[StoredObjectRef] = Field(default_factory=list)
    sections: list[str] | None = None
    deck_title: str | None = None
    options: GenerationOptions = Field(default_factory=GenerationOptions)
    deadline_at_utc: datetime | None = None


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

    contract_version: Literal["1.1"] = CONTRACT_VERSION
    job_id: str
    status: Literal["succeeded"] = "succeeded"
    artifacts: list[GeneratedArtifact]
    verification_passed: bool
    series_checked: int = Field(ge=0)
    external_checked: int = Field(ge=0)
    page_count: int = Field(ge=0)
    slide_count: int = Field(ge=0)
    chart_count: int = Field(ge=0)
