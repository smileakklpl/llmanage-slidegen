"""Dependency injection for the S3-backed job service."""

from functools import lru_cache

from app.core.config import settings
from app.repositories.s3_job_repository import S3JobRepository
from app.services.job_service import JobService
from app.storage.s3_storage import S3ObjectStorage


@lru_cache(maxsize=1)
def get_object_storage() -> S3ObjectStorage:
    """Return the process-wide S3 adapter with an explicitly configured region."""

    return S3ObjectStorage(
        bucket=settings.s3_bucket,
        region=settings.aws_region,
        endpoint_url=settings.s3_endpoint_url,
        presign_expires_seconds=settings.s3_presign_expires_seconds,
    )


@lru_cache(maxsize=1)
def get_job_service() -> JobService:
    storage = get_object_storage()
    repository = S3JobRepository(storage)
    return JobService(
        repository=repository,
        storage=storage,
        use_fake_llm=settings.generation_use_fake_llm,
        skip_semantic_review=settings.generation_skip_semantic_review,
    )
