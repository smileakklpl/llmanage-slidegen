"""History API routes — list, download, and delete generation history records."""

from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.dependencies import get_current_user
from app.auth.history import delete_history_record, get_history_record, list_history
from app.api.deps import get_object_storage

router = APIRouter(prefix="/auth/history", tags=["history"])


# ---------- Schemas ----------


class ArtifactItem(BaseModel):
    filename: str
    object_key: str
    type: str = ""
    size_bytes: int = 0


class HistoryRecord(BaseModel):
    record_id: str
    job_id: str
    prompt: str
    created_at: str
    artifacts: list[ArtifactItem] = []


class HistoryListResponse(BaseModel):
    records: list[HistoryRecord]
    count: int


class DownloadUrlResponse(BaseModel):
    url: str
    filename: str


class DeleteResponse(BaseModel):
    success: bool
    message: str


# ---------- Endpoints ----------


@router.get("", response_model=HistoryListResponse)
async def get_history(
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
) -> HistoryListResponse:
    """List generation history for the authenticated user."""
    records = list_history(current_user["email"], limit=limit)
    return HistoryListResponse(
        records=[
            HistoryRecord(
                record_id=r["record_id"],
                job_id=r["job_id"],
                prompt=r.get("prompt", ""),
                created_at=r["created_at"],
                artifacts=[
                    ArtifactItem(**a) for a in r.get("artifacts", [])
                ],
            )
            for r in records
        ],
        count=len(records),
    )


@router.get("/{record_id}/download/{filename}", response_model=DownloadUrlResponse)
async def download_history_artifact(
    record_id: str,
    filename: str,
    current_user: dict = Depends(get_current_user),
) -> DownloadUrlResponse:
    """Generate a presigned S3 download URL for a specific artifact."""
    record = get_history_record(current_user["email"], record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該歷史紀錄",
        )

    # Find the artifact by filename
    artifact = next(
        (a for a in record.get("artifacts", []) if a.get("filename") == filename),
        None,
    )
    if not artifact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"找不到檔案：{filename}",
        )

    storage = get_object_storage()
    try:
        url = storage.presigned_download_url(artifact["object_key"])
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="無法產生下載連結",
        ) from error

    return DownloadUrlResponse(url=url, filename=filename)


@router.delete("/{record_id}", response_model=DeleteResponse)
async def delete_history(
    record_id: str,
    current_user: dict = Depends(get_current_user),
) -> DeleteResponse:
    """Delete a generation history record."""
    record = get_history_record(current_user["email"], record_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="找不到該歷史紀錄",
        )

    delete_history_record(current_user["email"], record_id)
    return DeleteResponse(success=True, message="已刪除歷史紀錄")
