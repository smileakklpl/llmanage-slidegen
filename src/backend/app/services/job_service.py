"""Service layer for job orchestration.

JobService is responsible for creating jobs, generating unique IDs, and
coordinating with the repository. It does NOT depend on FastAPI.
"""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import JobStage, JobStatus
from app.worker.job_runner import run_job

# Temp directory for uploaded files
_UPLOAD_DIR = Path(tempfile.gettempdir()) / "slidegen_uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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

    async def _save_uploads(self, job_id: str, files: list[UploadFile]) -> list[str]:
        """Save uploaded files to a job-specific temp directory.

        Returns:
            List of absolute file paths where files were saved.
        """
        job_dir = _UPLOAD_DIR / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        paths: list[str] = []
        for file in files:
            filename = file.filename or "unknown.xlsx"
            dest = job_dir / filename
            content = await file.read()
            dest.write_bytes(content)
            paths.append(str(dest))

            # Upload to S3 (non-blocking, won't fail the job if S3 is disabled)
            from app.services.s3_service import store_upload
            store_upload(job_id, dest, filename)

        return paths

    async def create_job(
        self, prompt: str, filenames: list[str], files: list[UploadFile] | None = None
    ) -> JobModel:
        """Create a new job in queued state and persist it.

        Args:
            prompt: The user-provided prompt describing desired output.
            filenames: Names of the uploaded Excel files.
            files: The actual uploaded file objects to save to disk.

        Returns:
            The persisted JobModel with a unique job_id.
        """
        now = datetime.now(timezone.utc)
        job_id = self.generate_job_id()

        # Save files to disk if provided
        file_paths: list[str] = []
        if files:
            file_paths = await self._save_uploads(job_id, files)

        job = JobModel(
            job_id=job_id,
            status=JobStatus.queued,
            stage=JobStage.queued,
            progress=0,
            message="工作已排入佇列",
            created_at=now,
            updated_at=now,
            prompt=prompt,
            filenames=filenames,
            file_paths=file_paths,
        )
        created = await self._repository.create(job)

        # Start the job runner in the background (non-blocking).
        asyncio.create_task(run_job(created.job_id, self._repository))

        return created

    async def get_job(self, job_id: str) -> JobModel | None:
        """Retrieve a job by ID.

        Args:
            job_id: Unique identifier of the job to retrieve.

        Returns:
            The JobModel if found, otherwise None.
        """
        return await self._repository.get(job_id)
