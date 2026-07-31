"""Service layer for durable asynchronous generation jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import uuid4

from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import JobStage, JobStatus
from app.storage.s3_storage import S3ObjectStorage
from app.worker.generation_job_runner import run_generation_job
from core.contracts.generation import StoredObjectRef


def is_authorized_artifact_key(
    job_id: str,
    filename: str,
    object_key: str | None,
) -> bool:
    """Return whether an S3 key belongs to this job and artifact filename."""
    if not object_key:
        return False

    key_path = PurePosixPath(object_key)
    return (
        key_path.parts[:2] == ("outputs", job_id)
        and ".." not in key_path.parts
        and key_path.name == filename
    )


class JobService:
    """Create jobs and dispatch the real generation worker."""

    def __init__(
        self,
        repository: JobRepository,
        storage: S3ObjectStorage,
        *,
        use_fake_llm: bool = False,
        skip_semantic_review: bool = False,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._use_fake_llm = use_fake_llm
        self._skip_semantic_review = skip_semantic_review

    @staticmethod
    def generate_job_id() -> str:
        return str(uuid4())

    async def create_job(
        self,
        *,
        job_id: str,
        prompt: str,
        input_objects: list[StoredObjectRef],
    ) -> JobModel:
        """Persist a queued job and start its non-blocking worker task."""

        if not input_objects:
            raise ValueError("至少需要一個已保存的輸入物件")

        now = datetime.now(timezone.utc)
        job = JobModel(
            job_id=job_id,
            status=JobStatus.queued,
            stage=JobStage.queued,
            progress=0,
            message="工作已排入佇列",
            created_at=now,
            updated_at=now,
            prompt=prompt,
            filenames=[item.filename for item in input_objects],
            input_objects=input_objects,
        )
        created = await self._repository.create(job)

        asyncio.create_task(
            run_generation_job(
                created.job_id,
                self._repository,
                self._storage,
                use_fake_llm=self._use_fake_llm,
                skip_semantic_review=self._skip_semantic_review,
            )
        )
        return created

    async def get_job(
        self,
        job_id: str,
        *,
        refresh_download_urls: bool = True,
    ) -> JobModel | None:
        job = await self._repository.get(job_id)

        if job is None:
            return None

        if job.job_id != job_id:
            raise RuntimeError("持久化工作識別碼與查詢鍵不一致")

        if not job.artifacts:
            return job

        # Never trust a persisted URL. Only keys belonging to the requested
        # job may be signed, and callers can request sanitized metadata without
        # signing.
        artifacts = []
        for artifact in job.artifacts:
            authorized = is_authorized_artifact_key(
                job_id,
                artifact.filename,
                artifact.object_key,
            )
            download_url = (
                self._storage.presigned_download_url(artifact.object_key)
                if authorized
                and refresh_download_urls
                and artifact.object_key is not None
                else ""
            )
            artifacts.append(
                artifact.model_copy(update={"download_url": download_url})
            )

        return job.model_copy(update={"artifacts": artifacts})
