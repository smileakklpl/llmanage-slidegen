"""S3 implementation of the job repository contract."""

from __future__ import annotations

import asyncio

from app.repositories.job_repository import JobModel, JobRepository
from app.storage.s3_storage import S3ObjectStorage


class S3JobRepository(JobRepository):
    """Persist every job state transition as ``jobs/{job_id}.json`` in S3."""

    def __init__(self, storage: S3ObjectStorage) -> None:
        self._storage = storage

    @staticmethod
    def _key(job_id: str) -> str:
        return f"jobs/{job_id}.json"

    async def create(self, job: JobModel) -> JobModel:
        await asyncio.to_thread(
            self._storage.put_json,
            self._key(job.job_id),
            job.model_dump(mode="json"),
        )
        return job

    async def get(self, job_id: str) -> JobModel | None:
        payload = await asyncio.to_thread(
            self._storage.get_json,
            self._key(job_id),
        )
        return JobModel.model_validate(payload) if payload is not None else None

    async def update(self, job: JobModel) -> JobModel:
        await asyncio.to_thread(
            self._storage.put_json,
            self._key(job.job_id),
            job.model_dump(mode="json"),
        )
        return job
