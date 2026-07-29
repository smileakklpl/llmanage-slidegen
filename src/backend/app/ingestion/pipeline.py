from app.ingestion.security import (
    IngestionSecurityError,
    validate_ingestion_security,
)
from app.ingestion.normalizer import (
    build_unified_datasets,
)
from app.ingestion.visual_parser import (
    inspect_visual_input,
)
from app.ingestion.pdf_parser import (
    extract_pdf_tables,
    inspect_pdf_document,
)
from app.ingestion.delimited import (
    extract_delimited_table,
    inspect_delimited_content,
)
from pathlib import Path
from time import perf_counter

from app.ingestion.classifier import (
    inspect_excel_content,
)
from app.ingestion.detector import inspect_file
from app.ingestion.extractor import (
    extract_excel_tables,
)
from app.ingestion.schemas import (
    ContainerType,
    PipelineStageResult,
    PipelineStageStatus,
    PipelineStatus,
    QualityStatus,
    UnifiedIngestionResult,
    WorkbookTableExtraction,
)
from app.ingestion.validator import (
    validate_workbook_extraction,
)


def _duration_ms(start_time: float) -> float:
    """計算單一階段執行時間。"""
    return round(
        (perf_counter() - start_time) * 1000,
        2,
    )


def _update_extraction_filename(
    extraction: WorkbookTableExtraction,
    filename: str,
) -> WorkbookTableExtraction:
    """
    暫存檔名稱通常是隨機名稱，
    因此要換回使用者原始檔名。
    """
    updated_tables = [
        table.model_copy(
            update={
                "filename": filename,
            }
        )
        for table in extraction.tables
    ]

    return extraction.model_copy(
        update={
            "filename": filename,
            "tables": updated_tables,
        }
    )


def run_ingestion_pipeline(
    file_path: str | Path,
    original_filename: str | None = None,
    sheet_name: str | None = None,
) -> UnifiedIngestionResult:
    """
    執行完整資料讀取流程。

    目前正式支援：
    - XLSX
    - CSV／TSV／TXT 等分隔文字表格
    - 具有文字層或掃描內容的 PDF
    - PNG／JPEG 圖片與表格、圖表截圖

    其他格式會先完成格式偵測，
    再回傳 unsupported。
    """
    path = Path(file_path)

    display_filename = (
        original_filename or path.name
    )

    stages: list[PipelineStageResult] = []
    warnings: list[str] = []
    errors: list[str] = []

    inspection = None
    classification = None
    document = None
    visual = None
    visual_extraction = None
    extraction = None
    validation = None
    datasets = []
    review_required_count = 0
    # -------------------------------------------------
    # Stage 1：檔案格式偵測
    # -------------------------------------------------
    start_time = perf_counter()

    try:
        inspection = inspect_file(path)

        inspection = inspection.model_copy(
            update={
                "filename": display_filename,
            }
        )

        stages.append(
            PipelineStageResult(
                stage="file_inspection",
                status=(
                    PipelineStageStatus.COMPLETED
                ),
                message=(
                    "完成檔案格式與可讀性檢查"
                ),
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except Exception as error:
        error_message = (
            f"檔案格式偵測失敗：{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="file_inspection",
                status=PipelineStageStatus.FAILED,
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.FAILED,
            inspection=None,
            document=document,
            visual=visual,
            stages=stages,
            warnings=warnings,
            errors=errors,
            datasets=datasets,
            review_required_count=review_required_count,
        )

    warnings.extend(inspection.warnings)

    # -------------------------------------------------
    # 檔案拒絕條件
    # -------------------------------------------------
    if not inspection.is_readable:
        error_message = "檔案無法正常讀取"
        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="content_processing",
                status=PipelineStageStatus.SKIPPED,
                message=(
                    "因檔案無法讀取，"
                    "後續階段已停止"
                ),
                duration_ms=0,
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.REJECTED,
            inspection=inspection,
            document=document,
            visual=visual,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    if inspection.is_encrypted:
        error_message = (
            "檔案受到密碼或加密保護"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="content_processing",
                status=PipelineStageStatus.SKIPPED,
                message=(
                    "加密檔案目前不支援處理"
                ),
                duration_ms=0,
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.REJECTED,
            inspection=inspection,
            document=document,
            visual=visual,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )
    # -------------------------------------------------
    # 安全限制檢查
    # -------------------------------------------------
    start_time = perf_counter()

    try:
        validate_ingestion_security(
            file_path=path,
            inspection=inspection,
        )

        stages.append(
            PipelineStageResult(
                stage="security_validation",
                status=(
                    PipelineStageStatus.COMPLETED
                ),
                message="檔案安全檢查通過",
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except IngestionSecurityError as error:
        error_message = (
            f"檔案安全檢查未通過：{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="security_validation",
                status=(
                    PipelineStageStatus.FAILED
                ),
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=(
                PipelineStatus.REJECTED
            ),
            inspection=inspection,
            classification=None,
            document=document,
            visual=visual,
            extraction=None,
            validation=None,
            datasets=[],
            review_required_count=0,
            stages=stages,
            warnings=warnings,
            errors=errors,
        )
    # -------------------------------------------------
    # 目前支援表格文件、PDF 與視覺圖片
    # -------------------------------------------------
    supported_container_types = {
        ContainerType.XLSX,
        ContainerType.CSV,
        ContainerType.PDF,
        ContainerType.PNG,
        ContainerType.JPEG,
    }

    if (
        inspection.detected_container_type
        not in supported_container_types
    ):
        warning_message = (
            "目前統一 Pipeline 支援 XLSX、"
            "分隔文字表格、PDF、PNG 與 JPEG；"
            f"偵測到的格式為 "
            f"{inspection.detected_container_type.value}"
        )

        warnings.append(warning_message)

        stages.append(
            PipelineStageResult(
                stage="content_processing",
                status=PipelineStageStatus.SKIPPED,
                message=warning_message,
                duration_ms=0,
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.UNSUPPORTED,
            inspection=inspection,
            document=document,
            visual=visual,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------
    # Stage 2：內容分類
    # -------------------------------------------------
    start_time = perf_counter()

    try:
        if (
            inspection.detected_container_type
            == ContainerType.XLSX
        ):
            classification = inspect_excel_content(
                path
            )

        elif (
            inspection.detected_container_type
            == ContainerType.CSV
        ):
            classification = inspect_delimited_content(
                path
            )

        elif (
            inspection.detected_container_type
            == ContainerType.PDF
        ):
            (
                classification,
                document,
            ) = inspect_pdf_document(path)

            document = document.model_copy(
                update={
                    "filename": display_filename,
                }
            )

            # PDF 完全沒有文字層，或包含掃描頁時，
            # 再啟動視覺辨識流程。
            if (
                not document.has_text_layer
                or document.scanned_page_count > 0
            ):
                (
                    visual_classification,
                    visual,
                    visual_extraction,
                ) = inspect_visual_input(
                    path,
                    original_filename=(
                        display_filename
                    ),
                )

                # 整份 PDF 都沒有文字層時，
                # 以 OCR／版面模型分類作為主要分類。
                if not document.has_text_layer:
                    classification = (
                        visual_classification
                    )

        else:
            (
                classification,
                visual,
                visual_extraction,
            ) = inspect_visual_input(
                path,
                original_filename=(
                    display_filename
                ),
            )

        classification = (
            classification.model_copy(
                update={
                    "filename":
                        display_filename,
                }
            )
        )

        warnings.extend(
            classification.warnings
        )

        if visual is not None:
            warnings.extend(
                warning
                for warning in visual.warnings
                if warning not in warnings
            )

        stages.append(
            PipelineStageResult(
                stage="content_classification",
                status=(
                    PipelineStageStatus.COMPLETED
                ),
                message=(
                    f"完成 {classification.sheet_count} "
                    "個資料來源的內容分類"
                ),
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except Exception as error:
        error_message = (
            f"內容分類失敗：{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="content_classification",
                status=PipelineStageStatus.FAILED,
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.FAILED,
            inspection=inspection,
            classification=None,
            document=document,
            visual=visual,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------
    # Stage 3：表格抽取
    # -------------------------------------------------
    start_time = perf_counter()

    try:
        if (
            inspection.detected_container_type
            == ContainerType.XLSX
        ):
            extraction = extract_excel_tables(
                file_path=path,
                sheet_name=sheet_name,
            )

        elif (
            inspection.detected_container_type
            == ContainerType.CSV
        ):
            extraction = extract_delimited_table(
                file_path=path,
                sheet_name=sheet_name,
            )

        elif (
            inspection.detected_container_type
            == ContainerType.PDF
        ):
            extraction = extract_pdf_tables(
                file_path=path,
                sheet_name=sheet_name,
            )

            # 掃描頁若由 OCR 成功建立結構化表格，
            # 合併到 PDF 文字層所抽取的表格結果中。
            if (
                visual_extraction is not None
                and visual_extraction.table_count > 0
            ):
                extraction = WorkbookTableExtraction(
                    filename=display_filename,
                    table_count=(
                        extraction.table_count
                        + visual_extraction.table_count
                    ),
                    tables=(
                        extraction.tables
                        + visual_extraction.tables
                    ),
                    skipped_sheets=list(
                        dict.fromkeys(
                            extraction.skipped_sheets
                            + visual_extraction.skipped_sheets
                        )
                    ),
                    warnings=(
                        extraction.warnings
                        + visual_extraction.warnings
                    ),
                )

        else:
            # PNG／JPEG 的分類與抽取已在 Stage 2
            # 由 inspect_visual_input 一次完成。
            extraction = (
                visual_extraction
                or WorkbookTableExtraction(
                    filename=display_filename,
                    table_count=0,
                    tables=[],
                    skipped_sheets=["page_1"],
                    warnings=[
                        "沒有取得視覺表格抽取結果"
                    ],
                )
            )

        extraction = (
            _update_extraction_filename(
                extraction,
                display_filename,
            )
        )

        warnings.extend(
            extraction.warnings
        )

        extraction_stage_status = (
            PipelineStageStatus.COMPLETED
            if extraction.table_count > 0
            else PipelineStageStatus.WARNING
        )

        stages.append(
            PipelineStageResult(
                stage="table_extraction",
                status=extraction_stage_status,
                message=(
                    f"成功抽取 "
                    f"{extraction.table_count} 張表格；"
                    f"跳過 "
                    f"{len(extraction.skipped_sheets)} "
                    "個資料來源"
                ),
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except Exception as error:
        error_message = (
            f"表格抽取失敗：{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="table_extraction",
                status=PipelineStageStatus.FAILED,
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.FAILED,
            inspection=inspection,
            classification=classification,
            document=document,
            visual=visual,
            extraction=None,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------
    # Stage 4：資料品質驗證
    # -------------------------------------------------
    if extraction.table_count == 0:
        if document is not None:
            warning_message = (
                "PDF 文字內容已成功讀取，"
                "但沒有抽取到可驗證的表格"
            )
        else:
            warning_message = (
                "資料來源中沒有抽取到"
                "可進行品質驗證的表格"
            )

        warnings.append(warning_message)

        stages.append(
            PipelineStageResult(
                stage="data_validation",
                status=(
                    PipelineStageStatus.SKIPPED
                ),
                message=warning_message,
                duration_ms=0,
            )
        )

        stages.append(
            PipelineStageResult(
                stage="dataset_normalization",
                status=(
                    PipelineStageStatus.SKIPPED
                ),
                message=(
                    "沒有可轉換成統一資料集的表格"
                ),
                duration_ms=0,
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=(
                PipelineStatus
                .COMPLETED_WITH_WARNINGS
            ),
            inspection=inspection,
            classification=classification,
            document=document,
            visual=visual,
            extraction=extraction,
            validation=None,
            datasets=[],
            review_required_count=0,
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    start_time = perf_counter()

    try:
        validation = (
            validate_workbook_extraction(
                extraction
            )
        )

        if validation.status == QualityStatus.PASS:
            validation_stage_status = (
                PipelineStageStatus.COMPLETED
            )

            validation_message = (
                "資料品質驗證通過"
            )

        elif (
            validation.status
            == QualityStatus.WARNING
        ):
            validation_stage_status = (
                PipelineStageStatus.WARNING
            )

            validation_message = (
                "資料品質驗證完成，"
                f"發現 {validation.warning_count} "
                "個警告"
            )

        else:
            validation_stage_status = (
                PipelineStageStatus.WARNING
            )

            validation_message = (
                "資料品質驗證完成，"
                f"發現 {validation.error_count} "
                "個錯誤"
            )

        stages.append(
            PipelineStageResult(
                stage="data_validation",
                status=validation_stage_status,
                message=validation_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except Exception as error:
        error_message = (
            f"資料品質驗證失敗：{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage="data_validation",
                status=PipelineStageStatus.FAILED,
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=PipelineStatus.FAILED,
            inspection=inspection,
            classification=classification,
            document=document,
            visual=visual,
            extraction=extraction,
            validation=None,
            datasets=datasets,
            review_required_count=(
                review_required_count
            ),
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------
    # Stage 5：統一資料集與來源證據
    # -------------------------------------------------
    start_time = perf_counter()

    try:
        datasets = build_unified_datasets(
            extraction=extraction,
            inspection=inspection,
            validation=validation,
            visual=visual,
        )

        review_required_count = sum(
            dataset.requires_human_review
            for dataset in datasets
        )

        normalization_status = (
            PipelineStageStatus.WARNING
            if review_required_count > 0
            else PipelineStageStatus.COMPLETED
        )

        normalization_message = (
            f"建立 {len(datasets)} "
            "個統一資料集"
        )

        if review_required_count > 0:
            normalization_message += (
                f"；其中 "
                f"{review_required_count} 個"
                "需要人工確認"
            )

            warnings.append(
                f"{review_required_count} 個"
                "資料集需要人工確認"
            )

        stages.append(
            PipelineStageResult(
                stage=(
                    "dataset_normalization"
                ),
                status=(
                    normalization_status
                ),
                message=(
                    normalization_message
                ),
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

    except Exception as error:
        error_message = (
            "統一資料集建立失敗："
            f"{error}"
        )

        errors.append(error_message)

        stages.append(
            PipelineStageResult(
                stage=(
                    "dataset_normalization"
                ),
                status=(
                    PipelineStageStatus.FAILED
                ),
                message=error_message,
                duration_ms=_duration_ms(
                    start_time
                ),
            )
        )

        return UnifiedIngestionResult(
            filename=display_filename,
            pipeline_status=(
                PipelineStatus.FAILED
            ),
            inspection=inspection,
            classification=classification,
            document=document,
            visual=visual,
            extraction=extraction,
            validation=validation,
            datasets=[],
            review_required_count=0,
            stages=stages,
            warnings=warnings,
            errors=errors,
        )

    # -------------------------------------------------
    # 計算整條 Pipeline 最終狀態
    # -------------------------------------------------
    has_stage_warning = any(
        stage.status
        == PipelineStageStatus.WARNING
        for stage in stages
    )

    if (
        has_stage_warning
        or warnings
        or validation.status
        != QualityStatus.PASS
        or review_required_count > 0
    ):
        pipeline_status = (
            PipelineStatus
            .COMPLETED_WITH_WARNINGS
        )
    else:
        pipeline_status = (
            PipelineStatus.COMPLETED
        )

    return UnifiedIngestionResult(
        filename=display_filename,
        pipeline_status=pipeline_status,
        inspection=inspection,
        classification=classification,
        document=document,
        visual=visual,
        extraction=extraction,
        validation=validation,
        datasets=datasets,
        review_required_count=(
            review_required_count
        ),
        stages=stages,
        warnings=warnings,
        errors=errors,
    )