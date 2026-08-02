"""Abstract repository interface for job persistence.

This module defines the internal data model (JobModel) and the abstract
repository contract. It does NOT depend on FastAPI — only on Pydantic and
the standard library.
"""

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.jobs import Artifact, JobError, JobStage, JobStatus
from core.contracts.generation import (
    GenerationOptions,
    StoredObjectRef,
)


class JobModel(BaseModel):
    """Internal persistence model for a job.

    This is the repository's own representation, separate from the API
    response schema (JobStatusResponse). The service layer is responsible
    for mapping between the two.
    """

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
    prompt: str
    filenames: list[str]
    input_objects: list[StoredObjectRef] = Field(default_factory=list)
    template_object: StoredObjectRef | None = None
    ingestion_object: StoredObjectRef | None = None
    review_required_count: int = Field(default=0, ge=0)
    generation_options: GenerationOptions = Field(
        default_factory=GenerationOptions
    )
    user_email: str = ""


class JobRepository(ABC):
    """Abstract base class defining the job repository interface.

    Concrete implementations (e.g. MemoryJobRepository, S3JobRepository)
    must implement all abstract methods.
    """

    @abstractmethod
    async def create(self, job: JobModel) -> JobModel:
        """Persist a new job.

        Args:
            job: The job model to store.

        Returns:
            The persisted job model.
        """

    @abstractmethod
    async def get(self, job_id: str) -> JobModel | None:
        """Retrieve a job by its ID.

        Args:
            job_id: Unique identifier of the job.

        Returns:
            The job model if found, otherwise None.
        """

    @abstractmethod
    async def update(self, job: JobModel) -> JobModel:
        """Update an existing job.

        Args:
            job: The job model with updated fields.

        Returns:
            The updated job model.
        """
