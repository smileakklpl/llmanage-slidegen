"""Download endpoint for generated artifacts stored in S3."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.api.deps import get_job_service, get_object_storage
from app.core.errors import NotFoundError
from app.services.job_service import is_authorized_artifact_key

router = APIRouter(prefix="/downloads", tags=["downloads"])


@router.get("/{job_id}/{filename}")
async def download_artifact(job_id: str, filename: str) -> RedirectResponse:
    """Authorize an artifact and redirect to a newly signed S3 URL."""

    try:
        service = get_job_service()
        storage = get_object_storage()
        job = await service.get_job(job_id, refresh_download_urls=False)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="暫時無法讀取工作或產生下載連結",
        ) from error

    if job is None:
        raise NotFoundError(code="JOB_NOT_FOUND", message="找不到指定的工作")

    artifact = next(
        (item for item in job.artifacts if item.filename == filename),
        None,
    )
    object_key = artifact.object_key if artifact is not None else None

    if not is_authorized_artifact_key(job_id, filename, object_key):
        raise NotFoundError(
            code="FILE_NOT_FOUND",
            message="檔案尚未生成、已過期或不屬於此工作",
        )

    assert object_key is not None  # narrowed by is_authorized_artifact_key

    try:
        download_url = storage.presigned_download_url(object_key)
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="暫時無法產生下載連結",
        ) from error

    return RedirectResponse(url=download_url, status_code=307)
