"""Durable generation options and normalized-ingestion job contract."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.repositories.job_repository import JobModel
from app.schemas.jobs import JobStage, JobStatus
from core.contracts.generation import GenerationOptions, StoredObjectRef


def _object(filename: str = "input.xlsx") -> StoredObjectRef:
    return StoredObjectRef(
        bucket="slidegen",
        key=f"uploads/job/{filename}",
        filename=filename,
        size_bytes=123,
    )


def test_job_round_trip_persists_effective_delivery_options() -> None:
    now = datetime.now(timezone.utc)
    job = JobModel(
        job_id="job",
        status=JobStatus.queued,
        stage=JobStage.queued,
        progress=0,
        message="queued",
        created_at=now,
        updated_at=now,
        prompt="make deck",
        filenames=["input.xlsx"],
        input_objects=[_object()],
        generation_options=GenerationOptions(
            policy="required",
            deadline_seconds=120,
            render_reserve_seconds=30,
        ),
    )

    restored = JobModel.model_validate(job.model_dump(mode="json"))
    assert restored.generation_options.policy == "required"
    assert restored.generation_options.deadline_seconds == 120
    assert restored.generation_options.render_reserve_seconds == 30


def test_ingestion_object_is_a_durable_s3_reference() -> None:
    now = datetime.now(timezone.utc)
    ingestion = _object("ingestion.json")
    job = JobModel(
        job_id="job",
        status=JobStatus.running,
        stage=JobStage.analyzing_data,
        progress=20,
        message="ingested",
        created_at=now,
        updated_at=now,
        prompt="make deck",
        filenames=["input.xlsx"],
        input_objects=[_object()],
        ingestion_object=ingestion,
    )
    assert job.ingestion_object is not None
    assert job.ingestion_object.key.endswith("ingestion.json")


def test_render_reserve_cannot_consume_entire_deadline() -> None:
    with pytest.raises(ValidationError):
        GenerationOptions(
            deadline_seconds=30,
            render_reserve_seconds=30,
        )


def test_normalized_ingestion_contract_rejects_unknown_version() -> None:
    from core.contracts.generation import NormalizedIngestionContract

    with pytest.raises(ValidationError):
        NormalizedIngestionContract.model_validate(
            {
                "contract_version": "0.9",
                "filename": "input.xlsx",
                "pipeline_status": "completed",
                "source_files": ["input.xlsx"],
                "datasets": [{}],
            }
        )


def test_invalid_generation_options_map_to_http_422_before_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from io import BytesIO

    from fastapi import HTTPException, UploadFile

    from app.api import jobs
    from app.services.job_service import JobService

    service = JobService(object(), object())  # type: ignore[arg-type]
    monkeypatch.setattr(jobs, "get_job_service", lambda: service)
    monkeypatch.setattr(jobs, "get_object_storage", lambda: object())
    upload = UploadFile(filename="input.xlsx", file=BytesIO(b"xlsx"))

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            jobs.generate_job(
                files=[upload],
                prompt="make deck",
                generation_policy="invalid",
                generation_deadline_seconds=30,
                generation_render_reserve_seconds=5,
            )
        )

    assert captured.value.status_code == 422


def test_upload_fileobj_records_content_sha256() -> None:
    import hashlib
    from io import BytesIO

    from app.storage.s3_storage import S3ObjectStorage

    content = b"raw workbook bytes"

    class FakeClient:
        def upload_fileobj(self, stream, bucket, key, ExtraArgs=None):
            assert stream.read() == content

        def head_object(self, *, Bucket, Key):
            return {"ContentLength": len(content), "ETag": '"etag"'}

    storage = object.__new__(S3ObjectStorage)
    storage.bucket = "bucket"
    storage._client = FakeClient()
    ref = storage.upload_fileobj(
        BytesIO(content),
        key="uploads/job/input.xlsx",
        filename="input.xlsx",
    )

    assert ref.sha256 == hashlib.sha256(content).hexdigest()