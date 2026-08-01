import os
import tempfile
from pathlib import Path

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from app.ingestion.classifier import (
    inspect_excel_content,
)
from app.ingestion.detector import inspect_file
from app.ingestion.extractor import (
    extract_excel_tables,
)
from app.ingestion.normalizer import (
    apply_human_review,
)
from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.schemas import (
    ContainerType,
    DatasetReviewPayload,
    FileInspectionResult,
    UnifiedDatasetSpec,
    UnifiedIngestionResult,
    WorkbookContentInspection,
    WorkbookQualityReport,
    WorkbookTableExtraction,
)
from app.ingestion.settings import (
    MAX_UPLOAD_BYTES,
    UPLOAD_CHUNK_BYTES,
)
from app.ingestion.validator import (
    validate_workbook_extraction,
)


router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
)


async def _save_upload_safely(
    file: UploadFile,
) -> tuple[Path, str]:
    """以分段方式儲存上傳檔案，並統一套用大小限制。"""
    original_filename = Path(
        file.filename or "upload.bin"
    ).name
    suffix = Path(
        original_filename
    ).suffix.lower()

    file_descriptor, temporary_name = (
        tempfile.mkstemp(
            suffix=suffix,
        )
    )
    temporary_path = Path(
        temporary_name
    )
    total_size = 0

    try:
        with os.fdopen(
            file_descriptor,
            "wb",
        ) as output:
            while True:
                chunk = await file.read(
                    UPLOAD_CHUNK_BYTES
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "上傳檔案超過 "
                            f"{MAX_UPLOAD_BYTES} bytes 限制"
                        ),
                    )

                output.write(chunk)

        return (
            temporary_path,
            original_filename,
        )

    except Exception:
        temporary_path.unlink(
            missing_ok=True
        )
        raise

    finally:
        await file.close()


def _ensure_xlsx(
    inspection: FileInspectionResult,
) -> None:
    if (
        inspection.detected_container_type
        != ContainerType.XLSX
    ):
        raise HTTPException(
            status_code=415,
            detail=(
                "此端點目前只接受 XLSX，"
                "實際偵測到的格式為："
                f"{inspection.detected_container_type.value}"
            ),
        )


@router.post(
    "/inspect",
    response_model=FileInspectionResult,
)
async def inspect_uploaded_file(
    file: UploadFile = File(...),
) -> FileInspectionResult:
    """上傳檔案並判斷最外層格式。"""
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(file)

        result = await run_in_threadpool(
            inspect_file,
            temporary_path,
        )

        suffix = Path(
            original_filename
        ).suffix.lower()

        return result.model_copy(
            update={
                "filename": original_filename,
                "extension": suffix or None,
            }
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"檔案檢查失敗：{error}",
        ) from error

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )


@router.post(
    "/inspect-excel-content",
    response_model=WorkbookContentInspection,
)
async def inspect_uploaded_excel_content(
    file: UploadFile = File(...),
) -> WorkbookContentInspection:
    """掃描 Excel 中每一張工作表的內容類型。"""
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(file)

        inspection = await run_in_threadpool(
            inspect_file,
            temporary_path,
        )
        _ensure_xlsx(inspection)

        result = await run_in_threadpool(
            inspect_excel_content,
            temporary_path,
        )

        return result.model_copy(
            update={
                "filename": original_filename,
            }
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "Excel 內容檢查失敗："
                f"{error}"
            ),
        ) from error

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )


@router.post(
    "/extract-excel-tables",
    response_model=WorkbookTableExtraction,
)
async def extract_uploaded_excel_tables(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(
        default=None
    ),
) -> WorkbookTableExtraction:
    """抽取 Excel 中的結構化表格。"""
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(file)

        inspection = await run_in_threadpool(
            inspect_file,
            temporary_path,
        )
        _ensure_xlsx(inspection)

        result = await run_in_threadpool(
            extract_excel_tables,
            temporary_path,
            sheet_name,
        )

        updated_tables = [
            table.model_copy(
                update={
                    "filename": original_filename,
                }
            )
            for table in result.tables
        ]

        return result.model_copy(
            update={
                "filename": original_filename,
                "tables": updated_tables,
            }
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "Excel 表格抽取失敗："
                f"{error}"
            ),
        ) from error

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )


@router.post(
    "/validate-excel-data",
    response_model=WorkbookQualityReport,
)
async def validate_uploaded_excel_data(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(
        default=None
    ),
) -> WorkbookQualityReport:
    """抽取並驗證 Excel 資料品質。"""
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(file)

        inspection = await run_in_threadpool(
            inspect_file,
            temporary_path,
        )
        _ensure_xlsx(inspection)

        extraction = await run_in_threadpool(
            extract_excel_tables,
            temporary_path,
            sheet_name,
        )

        extraction = extraction.model_copy(
            update={
                "filename": original_filename,
                "tables": [
                    table.model_copy(
                        update={
                            "filename": original_filename,
                        }
                    )
                    for table in extraction.tables
                ],
            }
        )

        return await run_in_threadpool(
            validate_workbook_extraction,
            extraction,
        )

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=(
                "Excel 資料品質驗證失敗："
                f"{error}"
            ),
        ) from error

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )


@router.post(
    "/process",
    response_model=UnifiedIngestionResult,
)
async def process_uploaded_file(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(
        default=None
    ),
) -> UnifiedIngestionResult:
    """執行完整資料輸入、抽取、驗證與正規化流程。"""
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(file)

        return await run_in_threadpool(
            run_ingestion_pipeline,
            temporary_path,
            original_filename,
            sheet_name,
        )

    finally:
        if temporary_path is not None:
            temporary_path.unlink(
                missing_ok=True
            )


@router.post(
    "/review-dataset",
    response_model=UnifiedDatasetSpec,
)
async def review_dataset(
    payload: DatasetReviewPayload,
) -> UnifiedDatasetSpec:
    """通過、拒絕或修正需要人工確認的資料集。"""
    try:
        return apply_human_review(
            dataset=payload.dataset,
            review=payload.review,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error