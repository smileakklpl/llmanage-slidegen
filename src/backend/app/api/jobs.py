"""Job generation, status, and email send endpoints."""

import asyncio
import mimetypes
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app.api.deps import get_job_service, get_object_storage
from app.core.errors import NotFoundError
from app.ingestion.schemas import HumanReviewRequest, UnifiedDatasetSpec
from app.ingestion.settings import MAX_UPLOAD_BYTES
from app.schemas.jobs import (
    JobCreateResponse,
    JobReviewResponse,
    JobStatusResponse,
    ResumeJobResponse,
    ReviewSource,
    SendEmailResponse,
)
from core.contracts.generation import StoredObjectRef

router = APIRouter(prefix="/jobs", tags=["jobs"])

ALLOWED_UPLOAD_SUFFIXES = {
    ".xlsx", ".csv", ".tsv", ".txt", ".pdf", ".png", ".jpg", ".jpeg"
}


def _stream_size(stream: object) -> int:
    """Measure the actual upload stream without trusting multipart metadata."""
    tell = getattr(stream, "tell")
    seek = getattr(stream, "seek")
    current = tell()
    try:
        seek(0, 2)
        return int(tell())
    finally:
        seek(current)


async def _load_job(job_id: str):
    """Load a durable job and translate dependency/S3 failures to HTTP 503."""
    try:
        service = get_job_service()
        job = await service.get_job(job_id)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="暫時無法讀取工作狀態",
        ) from error

    if job is None:
        raise NotFoundError(code="JOB_NOT_FOUND", message="找不到指定的工作")

    return job


@router.post("/generate", response_model=JobCreateResponse, status_code=202)
async def generate_job(
    files: Annotated[list[UploadFile], File(...)],
    prompt: str = Form(...),
    generation_policy: str | None = Form(default=None),
    generation_deadline_seconds: float | None = Form(default=None),
    generation_render_reserve_seconds: float | None = Form(default=None),
) -> JobCreateResponse:
    """Persist supported uploads in S3 and queue ingestion/generation."""

    normalized_prompt = prompt.strip()

    if not normalized_prompt:
        raise HTTPException(status_code=422, detail="prompt 不可為空")

    if not files:
        raise HTTPException(status_code=422, detail="至少需要一個資料檔案")

    filenames = [Path(file.filename or "upload.xlsx").name for file in files]

    for file, filename in zip(files, filenames):
        if Path(filename).suffix.lower() not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail=(
                    f"不支援的格式：{filename}；目前接受 "
                    + ", ".join(sorted(ALLOWED_UPLOAD_SUFFIXES))
                ),
            )

        actual_size = await asyncio.to_thread(_stream_size, file.file)
        if actual_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"上傳檔案超過 {MAX_UPLOAD_BYTES} bytes 限制：{filename}",
            )

    try:
        service = get_job_service()
        storage = get_object_storage()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    try:
        service.resolve_generation_options(
            generation_policy=generation_policy,
            generation_deadline_seconds=generation_deadline_seconds,
            generation_render_reserve_seconds=(
                generation_render_reserve_seconds
            ),
        )
    except ValidationError as error:
        raise HTTPException(
            status_code=422,
            detail=error.errors(include_url=False),
        ) from error

    job_id = service.generate_job_id()
    uploaded: list[StoredObjectRef] = []

    try:
        for index, (file, filename) in enumerate(zip(files, filenames), start=1):
            await file.seek(0)
            content_type = file.content_type or mimetypes.guess_type(filename)[0]
            stored = await asyncio.to_thread(
                storage.upload_fileobj,
                file.file,
                key=f"uploads/{job_id}/{index:02d}_{filename}",
                filename=filename,
                content_type=content_type,
            )
            uploaded.append(stored)

        job = await service.create_job(
            job_id=job_id,
            prompt=normalized_prompt,
            input_objects=uploaded,
            generation_policy=generation_policy,
            generation_deadline_seconds=generation_deadline_seconds,
            generation_render_reserve_seconds=(
                generation_render_reserve_seconds
            ),
        )
    except HTTPException:
        raise
    except Exception as error:
        for item in uploaded:
            try:
                await asyncio.to_thread(storage.delete, item.key)
            except Exception:
                pass

        raise HTTPException(
            status_code=503,
            detail=f"無法保存上傳或建立工作：{type(error).__name__}",
        ) from error
    finally:
        for file in files:
            await file.close()

    return JobCreateResponse(
        job_id=job.job_id,
        status=job.status.value,
        status_url=f"/api/v1/jobs/{job.job_id}",
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Get the current status of a job."""
    job = await _load_job(job_id)

    # Only expose downloadable deliverables (pptx, xlsx) to users;
    # internal artifacts (verification.json, generation_manifest.json) are
    # kept in storage but not surfaced in the API response.
    user_artifacts = [a for a in job.artifacts if a.type != "json"]

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        message=job.message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        artifacts=user_artifacts,
        error=job.error,
        summary=job.summary,
        review_required_count=job.review_required_count,
        review_url=(
            f"/api/v1/jobs/{job.job_id}/review"
            if job.status == "waiting_review"
            else None
        ),
    )


@router.get("/{job_id}/review", response_model=JobReviewResponse)
async def get_job_review(job_id: str) -> JobReviewResponse:
    """Return persisted datasets plus source previews for human review."""
    try:
        service = get_job_service()
        storage = get_object_storage()
        job, payload = await service.get_review_payload(job_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    datasets = list(payload.get("datasets") or [])
    blocked = [
        item
        for item in datasets
        if item.get("requires_human_review")
        or item.get("review_status") in {"pending", "rejected"}
    ]
    sources = [
        ReviewSource(
            filename=item.filename,
            preview_url=storage.presigned_download_url(item.key),
        )
        for item in job.input_objects
        if item.key.startswith(f"uploads/{job_id}/")
    ]
    return JobReviewResponse(
        job_id=job_id,
        review_required_count=len(blocked),
        can_resume=not blocked,
        datasets=datasets,
        sources=sources,
    )


@router.post(
    "/{job_id}/datasets/{dataset_id}/review",
    response_model=UnifiedDatasetSpec,
)
async def review_job_dataset(
    job_id: str,
    dataset_id: str,
    review: HumanReviewRequest,
) -> UnifiedDatasetSpec:
    """Approve/reject/correct one persisted dataset."""
    try:
        service = get_job_service()
        _, dataset = await service.review_dataset(
            job_id=job_id,
            dataset_id=dataset_id,
            review=review,
        )
        return dataset
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/{job_id}/resume", response_model=ResumeJobResponse, status_code=202)
async def resume_job(job_id: str) -> ResumeJobResponse:
    """Resume a paused job after all datasets are approved."""
    try:
        job = await get_job_service().resume_job(job_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    return ResumeJobResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        message=job.message,
    )


@router.post("/{job_id}/send", response_model=SendEmailResponse)
async def send_job_email(
    job_id: str,
    sender: Annotated[str, Form(...)],
    recipients: Annotated[list[str], Form(...)],
    subject: str = Form(""),
    body: str = Form(""),
    artifact_filenames: list[str] = Form(default=[]),
    attachments: list[UploadFile] = File(default=[]),
) -> SendEmailResponse:
    """Send the completed job artifacts to the specified recipients via email.

    Accepts a multipart form with recipients, subject, body text, and
    optional file attachments.

    Behavior is controlled by EMAIL_PROVIDER env var:
    - "mock" (default): returns success without sending
    - "ses": sends real email via AWS SES with attachments
    """
    job = await _load_job(job_id)

    if job.status != "succeeded":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"code": "JOB_NOT_COMPLETED", "message": "工作尚未完成，無法寄送"},
        )

    # Read extra attachment files into memory
    extra_attachments: list[tuple[str, bytes]] = []
    for file in attachments:
        if file.filename:
            content = await file.read()
            extra_attachments.append((file.filename, content))

    # Build filename -> S3 object_key mapping from job artifacts
    artifact_object_keys: dict[str, str] = {
        artifact.filename: artifact.object_key
        for artifact in (job.artifacts or [])
        if artifact.object_key
    }

    # Send email (mock or real SES depending on EMAIL_PROVIDER env)
    from app.services.email_service import send_email

    storage = get_object_storage()
    result = await send_email(
        job_id=job_id,
        sender=sender,
        recipients=recipients,
        subject=subject,
        body=body,
        artifact_filenames=artifact_filenames,
        artifact_object_keys=artifact_object_keys,
        storage=storage,
        extra_attachments=extra_attachments,
    )

    return SendEmailResponse(
        job_id=result["job_id"],
        sender=result["sender"],
        recipients=result["recipients"],
        subject=result["subject"],
        attachment_count=result["attachment_count"],
        message=result["message"],
    )
