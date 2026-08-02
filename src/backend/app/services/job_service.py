"""Service layer for durable asynchronous generation jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Awaitable, Callable
from uuid import uuid4

from app.ingestion.normalizer import apply_human_review
from app.ingestion.schemas import HumanReviewRequest, UnifiedDatasetSpec
from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import JobStage, JobStatus
from app.storage.s3_storage import S3ObjectStorage
from app.worker.generation_job_runner import (
    resume_generation_job,
    run_generation_job,
)
from core.contracts.generation import (
    GenerationOptions,
    NormalizedIngestionContract,
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
        max_concurrent_jobs: int = 1,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._default_generation_options = (
            default_generation_options or GenerationOptions()
        )
        self._max_concurrent_jobs = max(1, int(max_concurrent_jobs))
        self._job_semaphore: asyncio.Semaphore | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def _run_bounded(
        self,
        job_id: str,
        runner: Callable[[str, JobRepository, S3ObjectStorage], Awaitable[None]],
    ) -> None:
        """Keep ingestion, generation and uploads inside one bounded job slot."""
        if self._job_semaphore is None:
            self._job_semaphore = asyncio.Semaphore(self._max_concurrent_jobs)

        async with self._job_semaphore:
            await runner(job_id, self._repository, self._storage)

    def _dispatch(
        self,
        job_id: str,
        runner: Callable[[str, JobRepository, S3ObjectStorage], Awaitable[None]],
    ) -> None:
        """Retain background tasks and apply the process-wide job bound."""
        task = asyncio.create_task(self._run_bounded(job_id, runner))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

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
        template_object: StoredObjectRef | None = None,
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
            template_object=template_object,
            generation_options=options,
        )
        created = await self._repository.create(job)

        self._dispatch(created.job_id, run_generation_job)
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
    async def get_review_payload(self, job_id: str) -> tuple[JobModel, dict]:
        """Return the persisted normalized ingestion payload for review UI."""
        job = await self._repository.get(job_id)
        if job is None:
            raise LookupError("找不到指定的工作")
        if job.ingestion_object is None:
            raise ValueError("此工作尚未建立 ingestion.json")

        payload = await asyncio.to_thread(
            self._storage.get_json,
            job.ingestion_object.key,
        )
        if payload is None:
            raise ValueError("找不到已保存的 ingestion.json")

        validated = NormalizedIngestionContract.model_validate(payload)
        return job, validated.model_dump(mode="json")

    async def review_dataset(
        self,
        *,
        job_id: str,
        dataset_id: str,
        review: HumanReviewRequest,
    ) -> tuple[JobModel, UnifiedDatasetSpec]:
        """Persist one dataset review back into the job's ingestion JSON."""
        job, payload = await self.get_review_payload(job_id)
        datasets = list(payload.get("datasets") or [])

        target_index = next(
            (
                index
                for index, item in enumerate(datasets)
                if item.get("dataset_id") == dataset_id
            ),
            None,
        )
        if target_index is None:
            raise LookupError(f"找不到資料集：{dataset_id}")

        raw_dataset = dict(datasets[target_index])
        reviewed = apply_human_review(
            UnifiedDatasetSpec.model_validate(raw_dataset),
            review,
        )
        # Preserve bridge-only extras such as backend_dataset_id.
        datasets[target_index] = raw_dataset | reviewed.model_dump(mode="json")
        payload["datasets"] = datasets

        validated = NormalizedIngestionContract.model_validate(payload)
        normalized_payload = validated.model_dump(mode="json")
        updated_ref = await asyncio.to_thread(
            self._storage.put_json_ref,
            job.ingestion_object.key,
            normalized_payload,
        )

        blocked = [
            dataset.dataset_id
            for dataset in validated.datasets
            if dataset.requires_human_review
            or dataset.review_status in {"pending", "rejected"}
        ]
        message = (
            f"尚有 {len(blocked)} 個資料集需要人工確認"
            if blocked
            else "所有資料集已通過人工確認，可繼續生成"
        )
        updated_job = job.model_copy(
            update={
                "status": JobStatus.waiting_review,
                "stage": JobStage.reviewing_data,
                "progress": 50 if not blocked else 45,
                "message": message,
                "review_required_count": len(blocked),
                "ingestion_object": updated_ref,
                "updated_at": datetime.now(timezone.utc),
                "error": None,
            }
        )
        updated_job = await self._repository.update(updated_job)
        return updated_job, reviewed

    async def resume_job(self, job_id: str) -> JobModel:
        """Resume generation only when every dataset has cleared review."""
        job, payload = await self.get_review_payload(job_id)
        if job.status != JobStatus.waiting_review:
            raise ValueError("只有 waiting_review 的工作可以續跑")

        ingestion = NormalizedIngestionContract.model_validate(payload)
        blocked = [
            dataset.dataset_id
            for dataset in ingestion.datasets
            if dataset.requires_human_review
            or dataset.review_status in {"pending", "rejected"}
        ]
        if blocked:
            raise ValueError(
                "仍有未通過人工確認的資料集：" + "、".join(blocked)
            )

        queued = job.model_copy(
            update={
                "status": JobStatus.queued,
                "stage": JobStage.queued,
                "progress": 50,
                "message": "人工確認完成，工作已重新排入生成佇列",
                "review_required_count": 0,
                "updated_at": datetime.now(timezone.utc),
                "error": None,
            }
        )
        queued = await self._repository.update(queued)
        self._dispatch(queued.job_id, resume_generation_job)
        return queued
