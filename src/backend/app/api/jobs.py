"""Job generation, status, and email send endpoints."""

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile

from app.api.deps import get_job_service
from app.core.errors import NotFoundError
from app.schemas.jobs import (
    JobCreateResponse,
    JobStatusResponse,
    SendEmailResponse,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/generate", response_model=JobCreateResponse, status_code=202)
async def generate_job(
    files: Annotated[list[UploadFile], File(...)],
    prompt: str = Form(...),
) -> JobCreateResponse:
    """Accept a multipart form with one or more Excel files and create a new job.

    Returns HTTP 202 immediately; the mock runner progresses the job
    in the background.
    """
    service = get_job_service()
    filenames = [f.filename or "unknown.xlsx" for f in files]
    job = await service.create_job(prompt=prompt, filenames=filenames)

    return JobCreateResponse(
        job_id=job.job_id,
        status=job.status.value,
        status_url=f"/api/v1/jobs/{job.job_id}",
    )


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Get the current status of a job."""
    service = get_job_service()
    job = await service.get_job(job_id)

    if job is None:
        raise NotFoundError(code="JOB_NOT_FOUND", message="找不到指定的工作")

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        stage=job.stage,
        progress=job.progress,
        message=job.message,
        created_at=job.created_at,
        updated_at=job.updated_at,
        artifacts=job.artifacts,
        error=job.error,
        summary=job.summary,
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
    optional file attachments. This is a mock implementation — no actual
    email is sent in this phase.
    """
    service = get_job_service()
    job = await service.get_job(job_id)

    if job is None:
        raise NotFoundError(code="JOB_NOT_FOUND", message="找不到指定的工作")

    if job.status != "succeeded":
        from fastapi import HTTPException

        raise HTTPException(
            status_code=400,
            detail={"code": "JOB_NOT_COMPLETED", "message": "工作尚未完成，無法寄送"},
        )

    extra_attachment_names = [f.filename or "unknown" for f in attachments if f.filename]
    total_attachments = len(artifact_filenames) + len(extra_attachment_names)

    return SendEmailResponse(
        job_id=job_id,
        sender=sender,
        recipients=recipients,
        subject=subject,
        attachment_count=total_attachments,
        message=(
            f"模擬寄送完成，由 {sender} 寄送給 {len(recipients)} 位收件者"
            + (f"，附帶 {total_attachments} 個附件" if total_attachments else "")
        ),
    )
