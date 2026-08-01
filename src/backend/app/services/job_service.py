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
from core.contracts.generation import (
    GenerationOptions,
    StoredObjectRef,
)


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
        default_generation_options: GenerationOptions | None = None,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._default_generation_options = (
            default_generation_options or GenerationOptions()
        )

    @staticmethod
    def generate_job_id() -> str:
        return str(uuid4())

    def resolve_generation_options(
        self,
        *,
        generation_policy: str | None = None,
        generation_deadline_seconds: float | None = None,
        generation_render_reserve_seconds: float | None = None,
    ) -> GenerationOptions:
        """Validate effective per-job options before any upload is persisted."""
        option_updates = {
            key: value
            for key, value in {
                "policy": generation_policy,
                "deadline_seconds": generation_deadline_seconds,
                "render_reserve_seconds": generation_render_reserve_seconds,
            }.items()
            if value is not None
        }
        return GenerationOptions.model_validate(
            self._default_generation_options.model_dump(mode="json")
            | option_updates
        )

    async def create_job(
        self,
        *,
        job_id: str,
        prompt: str,
        input_objects: list[StoredObjectRef],
        generation_policy: str | None = None,
        generation_deadline_seconds: float | None = None,
        generation_render_reserve_seconds: float | None = None,
    ) -> JobModel:
        """Persist a queued job and start its non-blocking worker task."""

        if not input_objects:
            raise ValueError("至少需要一個已保存的輸入物件")

        options = self.resolve_generation_options(
            generation_policy=generation_policy,
            generation_deadline_seconds=generation_deadline_seconds,
            generation_render_reserve_seconds=(
                generation_render_reserve_seconds
            ),
        )
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
            generation_options=options,
        )
        created = await self._repository.create(job)

        asyncio.create_task(
            run_generation_job(
                created.job_id,
                self._repository,
                self._storage,
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
