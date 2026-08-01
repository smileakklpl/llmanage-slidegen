from app.ingestion.normalizer import (
    apply_human_review,
)
from starlette.concurrency import run_in_threadpool

from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.validator import (
    validate_workbook_extraction,
)
from app.ingestion.classifier import inspect_excel_content
from app.ingestion.schemas import (
    ContainerType,
    FileInspectionResult,
    WorkbookContentInspection,
)
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
from app.ingestion.detector import inspect_file
from app.ingestion.schemas import (FileInspectionResult,DatasetReviewPayload,
    UnifiedDatasetSpec,
)
from app.ingestion.extractor import (
    extract_excel_tables,
)
from app.ingestion.schemas import (
    ContainerType,
    FileInspectionResult,
    UnifiedIngestionResult,
    WorkbookContentInspection,
    WorkbookQualityReport,
    WorkbookTableExtraction,
)
from app.ingestion.settings import (
    MAX_UPLOAD_BYTES,
    UPLOAD_CHUNK_BYTES,
)
async def _save_upload_safely(
    file: UploadFile,
) -> tuple[Path, str]:
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
                            f"{MAX_UPLOAD_BYTES} "
                            "bytes 限制"
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
router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
)

MAX_UPLOAD_SIZE = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


@router.post(
    "/inspect",
    response_model=FileInspectionResult,
)
async def inspect_uploaded_file(
    file: UploadFile = File(...),
) -> FileInspectionResult:
    """
    上傳檔案並判斷最外層格式。

    目前只進行 container type 偵測，
    尚未判斷內容是否為財報、表格或圖表。
    """
    original_filename = file.filename or "uploaded_file"
    suffix = Path(original_filename).suffix

    temporary_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temporary_file:
            temporary_path = temporary_file.name
            total_size = 0

            while True:
                chunk = await file.read(CHUNK_SIZE)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="檔案超過 25 MB 上限",
                    )

                temporary_file.write(chunk)

        result = inspect_file(temporary_path)

        # 暫存檔名稱是隨機值，回傳時換回使用者原始檔名。
        return result.model_copy(
            update={
                "filename": original_filename,
                "extension": (
                    suffix.lower()
                    if suffix
                    else None
                ),
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
        await file.close()

        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)

@router.post(
    "/inspect-excel-content",
    response_model=WorkbookContentInspection,
)
async def inspect_uploaded_excel_content(
    file: UploadFile = File(...),
) -> WorkbookContentInspection:
    """
    掃描 Excel 中每一張工作表的內容類型。

    目前只支援 .xlsx。
    """
    original_filename = file.filename or "uploaded.xlsx"
    suffix = Path(original_filename).suffix

    temporary_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temporary_file:
            temporary_path = temporary_file.name
            total_size = 0

            while True:
                chunk = await file.read(CHUNK_SIZE)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_UPLOAD_SIZE:
                    raise HTTPException(
                        status_code=413,
                        detail="檔案超過 25 MB 上限",
                    )

                temporary_file.write(chunk)

        file_inspection = inspect_file(temporary_path)

        if (
            file_inspection.detected_container_type
            != ContainerType.XLSX
        ):
            raise HTTPException(
                status_code=415,
                detail=(
                    "此端點目前只接受 XLSX，"
                    "實際偵測到的格式為："
                    f"{file_inspection.detected_container_type.value}"
                ),
            )

        result = inspect_excel_content(temporary_path)

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
            detail=f"Excel 內容檢查失敗：{error}",
        ) from error

    finally:
        await file.close()

        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)

@router.post(
    "/extract-excel-tables",
    response_model=WorkbookTableExtraction,
)
async def extract_uploaded_excel_tables(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
) -> WorkbookTableExtraction:
    """
    抽取 Excel 中的結構化表格。

    sheet_name 留空：
    抽取所有可辨識表格。

    sheet_name 有值：
    只抽取指定工作表。
    """
    original_filename = (
        file.filename or "uploaded.xlsx"
    )

    suffix = Path(
        original_filename
    ).suffix

    temporary_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temporary_file:
            temporary_path = temporary_file.name
            total_size = 0

            while True:
                chunk = await file.read(
                    CHUNK_SIZE
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if (
                    total_size
                    > MAX_UPLOAD_SIZE
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "檔案超過 25 MB 上限"
                        ),
                    )

                temporary_file.write(chunk)

        file_inspection = inspect_file(
            temporary_path
        )

        if (
            file_inspection
            .detected_container_type
            != ContainerType.XLSX
        ):
            raise HTTPException(
                status_code=415,
                detail=(
                    "此端點目前只接受 XLSX，"
                    "實際偵測格式為："
                    f"{file_inspection.detected_container_type.value}"
                ),
            )

        result = extract_excel_tables(
            file_path=temporary_path,
            sheet_name=sheet_name,
        )

        updated_tables = [
            table.model_copy(
                update={
                    "filename": original_filename
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
                f"Excel 表格抽取失敗："
                f"{error}"
            ),
        ) from error

    finally:
        await file.close()

        if (
            temporary_path
            and os.path.exists(
                temporary_path
            )
        ):
            os.remove(temporary_path)    

@router.post(
    "/validate-excel-data",
    response_model=WorkbookQualityReport,
)
async def validate_uploaded_excel_data(
    file: UploadFile = File(...),
    sheet_name: str | None = Form(default=None),
) -> WorkbookQualityReport:
    """
    抽取並驗證 Excel 資料品質。

    驗證內容包括：
    - 缺失值
    - 型態錯誤
    - 重複資料列
    - 百分比尺度
    - 公式結果
    - 資產負債表平衡
    """
    original_filename = (
        file.filename or "uploaded.xlsx"
    )

    suffix = Path(
        original_filename
    ).suffix

    temporary_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temporary_file:
            temporary_path = temporary_file.name
            total_size = 0

            while True:
                chunk = await file.read(
                    CHUNK_SIZE
                )

                if not chunk:
                    break

                total_size += len(chunk)

                if (
                    total_size
                    > MAX_UPLOAD_SIZE
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "檔案超過 25 MB 上限"
                        ),
                    )

                temporary_file.write(chunk)

        file_inspection = inspect_file(
            temporary_path
        )

        if (
            file_inspection
            .detected_container_type
            != ContainerType.XLSX
        ):
            raise HTTPException(
                status_code=415,
                detail=(
                    "此端點目前只接受 XLSX，"
                    "實際偵測格式為："
                    f"{file_inspection.detected_container_type.value}"
                ),
            )

        extraction = extract_excel_tables(
            file_path=temporary_path,
            sheet_name=sheet_name,
        )

        extraction = extraction.model_copy(
            update={
                "filename": original_filename,
                "tables": [
                    table.model_copy(
                        update={
                            "filename":
                                original_filename
                        }
                    )
                    for table
                    in extraction.tables
                ],
            }
        )

        return validate_workbook_extraction(
            extraction
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
        await file.close()

        if (
            temporary_path
            and os.path.exists(
                temporary_path
            )
        ):
            os.remove(temporary_path)   

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
    temporary_path: Path | None = None

    try:
        (
            temporary_path,
            original_filename,
        ) = await _save_upload_safely(
            file
        )

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
    """
    對需要人工確認的資料集進行：
    - 通過
    - 拒絕
    - 修正後通過

    目前為無狀態 API，
    第十階段再接資料庫保存。
    """
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