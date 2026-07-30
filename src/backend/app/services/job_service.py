"""Service layer for job orchestration.

JobService is responsible for creating jobs, generating unique IDs, and
coordinating with the repository. It does NOT depend on FastAPI.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import JobStage, JobStatus
from app.worker.mock_job_runner import run_mock_job


class JobService:
    """Handles job creation and lifecycle management.

    Dependencies are injected via the constructor so the service remains
    decoupled from specific repository implementations.
    """

    def __init__(self, repository: JobRepository) -> None:
        self._repository = repository

    @staticmethod
    def generate_job_id() -> str:
        """Generate a unique job identifier using UUID4."""
        return str(uuid4())

    async def create_job(
        self, prompt: str, filenames: list[str]
    ) -> JobModel:
        """Create a new job in queued state and persist it.

        Args:
            prompt: The user-provided prompt describing desired output.
            filenames: Names of the uploaded Excel files.

        Returns:
            The persisted JobModel with a unique job_id.
        """
        now = datetime.now(timezone.utc)
        job = JobModel(
            job_id=self.generate_job_id(),
            status=JobStatus.queued,
            stage=JobStage.queued,
            progress=0,
            message="工作已排入佇列",
            created_at=now,
            updated_at=now,
            prompt=prompt,
            filenames=filenames,
        )
        created = await self._repository.create(job)

        # Start the mock job runner in the background (non-blocking).
        asyncio.create_task(run_mock_job(created.job_id, self._repository))

        return created

    async def get_job(self, job_id: str) -> JobModel | None:
        """Retrieve a job by ID.

        Args:
            job_id: Unique identifier of the job to retrieve.

        Returns:
            The JobModel if found, otherwise None.
        """
        return await self._repository.get(job_id)
