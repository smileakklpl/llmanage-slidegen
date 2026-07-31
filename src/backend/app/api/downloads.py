"""File download endpoint for job artifacts."""

import tempfile
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.core.errors import NotFoundError

router = APIRouter(prefix="/downloads", tags=["downloads"])

# Must match the output dir used in job_runner.py
_OUTPUT_BASE = Path(tempfile.gettempdir()) / "slidegen_outputs"

# Allowed filenames to prevent path traversal
_ALLOWED_FILENAMES = {"deck.pptx", "deck_data.xlsx"}


@router.get("/{job_id}/{filename}")
async def download_artifact(job_id: str, filename: str) -> FileResponse:
    """Download a generated artifact file.

    Args:
        job_id: The job that produced the artifact.
        filename: The artifact filename (deck.pptx or deck_data.xlsx).
    """
    # Security: only allow known filenames
    if filename not in _ALLOWED_FILENAMES:
        raise NotFoundError(code="FILE_NOT_FOUND", message="找不到指定的檔案")

    file_path = _OUTPUT_BASE / job_id / filename

    if not file_path.exists():
        raise NotFoundError(code="FILE_NOT_FOUND", message="檔案尚未生成或已過期")

    # Determine media type
    media_type = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        if filename.endswith(".pptx")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=media_type,
    )
