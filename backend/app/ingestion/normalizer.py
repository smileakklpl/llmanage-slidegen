import json
from hashlib import sha256
from typing import Any

from app.ingestion.schemas import (
    TableDatasetSpec,
)
import re
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from app.ingestion.schemas import (
    ContainerType,
    DatasetCorrection,
    EvidenceType,
    FileInspectionResult,
    HumanReviewDecision,
    HumanReviewRequest,
    QualityStatus,
    ReviewStatus,
    SourceEvidence,
    SourceKind,
    UnifiedDatasetRecord,
    UnifiedDatasetSpec,
    UnifiedDataValue,
    VisualDocumentContent,
    WorkbookQualityReport,
    WorkbookTableExtraction,
)


VISUAL_TABLE_PATTERN = re.compile(
    r"^visual_page_(\d+)_table_(\d+)$"
)

PDF_TABLE_PATTERN = re.compile(
    r"^page_(\d+)_table_(\d+)$"
)


SOURCE_BASE_CONFIDENCE = {
    SourceKind.EXCEL: 0.99,
    SourceKind.DELIMITED_TEXT: 0.98,
    SourceKind.PDF_TEXT_TABLE: 0.90,
    SourceKind.OCR_IMAGE_TABLE: 0.80,
    SourceKind.OCR_PDF_TABLE: 0.80,
    SourceKind.UNKNOWN: 0.50,
}


EXTRACTION_METHODS = {
    SourceKind.EXCEL: "openpyxl",
    SourceKind.DELIMITED_TEXT: "python_csv",
    SourceKind.PDF_TEXT_TABLE: "pdfplumber",
    SourceKind.OCR_IMAGE_TABLE: (
        "paddleocr_ppstructurev3"
    ),
    SourceKind.OCR_PDF_TABLE: (
        "paddleocr_ppstructurev3"
    ),
    SourceKind.UNKNOWN: "unknown",
}


def _parse_page_and_table(
    sheet_name: str,
) -> tuple[int | None, int | None]:
    visual_match = (
        VISUAL_TABLE_PATTERN.fullmatch(
            sheet_name
        )
    )

    if visual_match:
        return (
            int(visual_match.group(1)),
            int(visual_match.group(2)),
        )

    pdf_match = (
        PDF_TABLE_PATTERN.fullmatch(
            sheet_name
        )
    )

    if pdf_match:
        return (
            int(pdf_match.group(1)),
            int(pdf_match.group(2)),
        )

    return None, None


def _determine_source_kind(
    inspection: FileInspectionResult,
    sheet_name: str,
) -> SourceKind:
    container_type = (
        inspection.detected_container_type
    )

    if container_type == ContainerType.XLSX:
        return SourceKind.EXCEL

    if container_type == ContainerType.CSV:
        return SourceKind.DELIMITED_TEXT

    if container_type == ContainerType.PDF:
        if sheet_name.startswith(
            "visual_page_"
        ):
            return SourceKind.OCR_PDF_TABLE

        return SourceKind.PDF_TEXT_TABLE

    if container_type in {
        ContainerType.PNG,
        ContainerType.JPEG,
    }:
        return SourceKind.OCR_IMAGE_TABLE

    return SourceKind.UNKNOWN


def _visual_maps(
    visual: VisualDocumentContent | None,
) -> tuple[
    dict[str, float],
    dict[int, bool],
]:
    confidence_map: dict[str, float] = {}
    review_map: dict[int, bool] = {}

    if visual is None:
        return confidence_map, review_map

    for page in visual.pages:
        review_map[page.page_number] = (
            page.requires_human_review
        )

        for table in page.tables:
            key = (
                f"visual_page_"
                f"{page.page_number}_"
                f"table_{table.table_index}"
            )

            confidence_map[key] = (
                table.confidence
            )

    return confidence_map, review_map


def _apply_validation_penalty(
    confidence: float,
    validation: (
        WorkbookQualityReport | None
    ),
) -> tuple[float, list[str]]:
    reasons: list[str] = []

    if validation is None:
        return confidence, reasons

    if validation.status == QualityStatus.WARNING:
        confidence -= 0.05

        reasons.append(
            "資料品質驗證產生警告"
        )

    elif validation.status == QualityStatus.FAIL:
        confidence -= 0.15

        reasons.append(
            "資料品質驗證未通過"
        )

    return (
        max(0.0, min(confidence, 1.0)),
        reasons,
    )


def _table_confidence(
    source_kind: SourceKind,
    sheet_name: str,
    visual_confidence_map: dict[
        str,
        float,
    ],
    validation: (
        WorkbookQualityReport | None
    ),
) -> tuple[float, list[str]]:
    confidence = SOURCE_BASE_CONFIDENCE[
        source_kind
    ]

    if sheet_name in visual_confidence_map:
        confidence = (
            visual_confidence_map[
                sheet_name
            ]
        )

    return _apply_validation_penalty(
        confidence,
        validation,
    )


def _review_reasons(
    source_kind: SourceKind,
    confidence: float,
    table_warnings: list[str],
    page_number: int | None,
    page_review_map: dict[int, bool],
    validation_reasons: list[str],
) -> list[str]:
    reasons = list(validation_reasons)

    if source_kind in {
        SourceKind.OCR_IMAGE_TABLE,
        SourceKind.OCR_PDF_TABLE,
    }:
        reasons.append(
            "資料由 OCR 與版面模型辨識"
        )

    if confidence < 0.85:
        reasons.append(
            "資料集信心分數低於 0.85"
        )

    if (
        page_number is not None
        and page_review_map.get(
            page_number,
            False,
        )
    ):
        reasons.append(
            "來源頁面被標記為需要人工確認"
        )

    for warning in table_warnings:
        normalized_warning = (
            warning.lower()
        )

        if any(
            keyword in normalized_warning
            for keyword in (
                "人工確認",
                "ocr",
                "推估",
                "合併儲存格",
            )
        ):
            reasons.append(warning)

    return list(dict.fromkeys(reasons))


def _json_default(
    value: Any,
) -> str:
    isoformat = getattr(
        value,
        "isoformat",
        None,
    )

    if callable(isoformat):
        return isoformat()

    return str(value)


def _dataset_id(
    table: TableDatasetSpec,
) -> str:
    payload = {
        "filename": table.filename,
        "sheet_name": table.sheet_name,
        "full_range": table.full_range,
        "columns": [
            {
                "key": column.key,
                "label": column.label,
                "data_type":
                    column.data_type.value,
                "unit": column.unit,
            }
            for column in table.columns
        ],
        "rows": [
            {
                key: cell.value
                for key, cell
                in row.cells.items()
            }
            for row in table.rows
        ],
    }

    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )

    content_hash = sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()

    return str(
        uuid5(
            NAMESPACE_URL,
            content_hash,
        )
    )

def build_unified_datasets(
    extraction: WorkbookTableExtraction,
    inspection: FileInspectionResult,
    validation: (
        WorkbookQualityReport | None
    ) = None,
    visual: (
        VisualDocumentContent | None
    ) = None,
) -> list[UnifiedDatasetSpec]:
    """
    將 Excel、CSV、PDF、OCR 表格統一轉成
    UnifiedDatasetSpec。
    """
    datasets: list[
        UnifiedDatasetSpec
    ] = []

    (
        visual_confidence_map,
        page_review_map,
    ) = _visual_maps(visual)

    for table in extraction.tables:
        source_kind = (
            _determine_source_kind(
                inspection=inspection,
                sheet_name=table.sheet_name,
            )
        )

        extraction_method = (
            EXTRACTION_METHODS[
                source_kind
            ]
        )

        (
            page_number,
            table_index,
        ) = _parse_page_and_table(
            table.sheet_name
        )

        (
            table_confidence,
            validation_reasons,
        ) = _table_confidence(
            source_kind=source_kind,
            sheet_name=table.sheet_name,
            visual_confidence_map=(
                visual_confidence_map
            ),
            validation=validation,
        )

        review_reasons = _review_reasons(
            source_kind=source_kind,
            confidence=table_confidence,
            table_warnings=table.warnings,
            page_number=page_number,
            page_review_map=page_review_map,
            validation_reasons=(
                validation_reasons
            ),
        )

        requires_review = bool(
            review_reasons
        )

        table_evidence = SourceEvidence(
            evidence_type=(
                EvidenceType.TABLE_RANGE
            ),
            source_kind=source_kind,
            filename=table.filename,
            sheet_name=table.sheet_name,
            page_number=page_number,
            table_index=table_index,
            cell_range=table.full_range,
            extraction_method=(
                extraction_method
            ),
            confidence=table_confidence,
            note=(
                "原始抽取表格範圍"
            ),
        )

        records: list[
            UnifiedDatasetRecord
        ] = []

        for record_index, row in enumerate(
            table.rows
        ):
            unified_values: dict[
                str,
                UnifiedDataValue,
            ] = {}

            value_confidences: list[
                float
            ] = []

            for column in table.columns:
                extracted_cell = (
                    row.cells.get(
                        column.key
                    )
                )

                if extracted_cell is None:
                    continue

                cell_confidence = (
                    table_confidence
                )

                if extracted_cell.value is None:
                    cell_confidence = max(
                        0.0,
                        table_confidence - 0.05,
                    )

                source = extracted_cell.source

                cell_evidence = (
                    SourceEvidence(
                        evidence_type=(
                            EvidenceType.CELL
                        ),
                        source_kind=source_kind,
                        filename=table.filename,
                        sheet_name=(
                            source.sheet
                        ),
                        page_number=(
                            page_number
                        ),
                        table_index=(
                            table_index
                        ),
                        cell=source.cell,
                        extraction_method=(
                            extraction_method
                        ),
                        confidence=(
                            cell_confidence
                        ),
                        note=(
                            "公式："
                            f"{extracted_cell.formula}"
                            if (
                                extracted_cell.formula
                                is not None
                            )
                            else None
                        ),
                    )
                )

                unified_values[column.key] = (
                    UnifiedDataValue(
                        raw_value=(
                            extracted_cell
                            .raw_value
                        ),
                        value=(
                            extracted_cell.value
                        ),
                        confidence=(
                            cell_confidence
                        ),
                        evidence=[
                            cell_evidence
                        ],
                        requires_human_review=(
                            requires_review
                        ),
                    )
                )

                value_confidences.append(
                    cell_confidence
                )

            record_confidence = (
                sum(value_confidences)
                / len(value_confidences)
                if value_confidences
                else table_confidence
            )

            records.append(
                UnifiedDatasetRecord(
                    record_index=(
                        record_index
                    ),
                    source_row=row.excel_row,
                    values=unified_values,
                    confidence=round(
                        record_confidence,
                        4,
                    ),
                    requires_human_review=(
                        requires_review
                    ),
                )
            )

        if records:
            dataset_confidence = sum(
                record.confidence
                for record in records
            ) / len(records)
        else:
            dataset_confidence = (
                table_confidence
            )

        dataset_confidence = round(
            min(
                dataset_confidence,
                table_confidence,
            ),
            4,
        )

        dataset_name = (
            table.metadata.title
            or table.sheet_name
        )

        datasets.append(
            UnifiedDatasetSpec(
                dataset_id=_dataset_id(table),
                name=dataset_name,
                filename=table.filename,
                source_container_type=(
                    inspection
                    .detected_container_type
                ),
                source_kind=source_kind,
                table_kind=table.table_kind,
                financial_statement_subtype=(
                    table
                    .financial_statement_subtype
                ),
                metadata=table.metadata,
                row_count=len(records),
                column_count=len(
                    table.columns
                ),
                columns=table.columns,
                records=records,
                confidence=(
                    dataset_confidence
                ),
                requires_human_review=(
                    requires_review
                ),
                review_status=(
                    ReviewStatus.PENDING
                    if requires_review
                    else ReviewStatus
                    .NOT_REQUIRED
                ),
                review_reasons=(
                    review_reasons
                ),
                evidence=[
                    table_evidence
                ],
                warnings=table.warnings,
            )
        )

    return datasets


def _find_record_position(
    dataset: UnifiedDatasetSpec,
    record_index: int,
) -> int:
    for position, record in enumerate(
        dataset.records
    ):
        if (
            record.record_index
            == record_index
        ):
            return position

    raise ValueError(
        f"找不到 record_index="
        f"{record_index}"
    )


def _apply_corrections(
    dataset: UnifiedDatasetSpec,
    corrections: list[
        DatasetCorrection
    ],
    reviewer: str,
) -> list[UnifiedDatasetRecord]:
    records = [
        record.model_copy(deep=True)
        for record in dataset.records
    ]

    valid_column_keys = {
        column.key
        for column in dataset.columns
    }

    for correction in corrections:
        if (
            correction.column_key
            not in valid_column_keys
        ):
            raise ValueError(
                "找不到欄位："
                f"{correction.column_key}"
            )

        position = _find_record_position(
            dataset,
            correction.record_index,
        )

        record = records[position]

        if (
            correction.column_key
            not in record.values
        ):
            raise ValueError(
                "資料列中不存在欄位："
                f"{correction.column_key}"
            )

        original_value = record.values[
            correction.column_key
        ]

        human_evidence = SourceEvidence(
            evidence_type=(
                EvidenceType.HUMAN_REVIEW
            ),
            source_kind=dataset.source_kind,
            filename=dataset.filename,
            sheet_name=(
                original_value
                .evidence[0]
                .sheet_name
                if original_value.evidence
                else None
            ),
            extraction_method=(
                "human_review"
            ),
            confidence=1.0,
            note=(
                f"由 {reviewer} 人工修正。"
                + (
                    f" {correction.note}"
                    if correction.note
                    else ""
                )
            ),
        )

        updated_value = (
            original_value.model_copy(
                update={
                    "value": (
                        correction
                        .corrected_value
                    ),
                    "confidence": 1.0,
                    "requires_human_review":
                        False,
                    "evidence": (
                        original_value
                        .evidence
                        + [human_evidence]
                    ),
                }
            )
        )

        updated_values = dict(
            record.values
        )

        updated_values[
            correction.column_key
        ] = updated_value

        records[position] = (
            record.model_copy(
                update={
                    "values": updated_values,
                }
            )
        )

    return records


def apply_human_review(
    dataset: UnifiedDatasetSpec,
    review: HumanReviewRequest,
) -> UnifiedDatasetSpec:
    """
    套用人工確認結果。

    此函式不負責資料庫保存；
    第十階段再接上持久化層。
    """
    reviewed_at = datetime.now(
        timezone.utc
    )

    if (
        review.decision
        == HumanReviewDecision.REJECT
    ):
        return dataset.model_copy(
            update={
                "review_status": (
                    ReviewStatus.REJECTED
                ),
                "requires_human_review":
                    True,
                "reviewed_by":
                    review.reviewer,
                "reviewed_at":
                    reviewed_at,
                "review_notes":
                    review.notes,
            }
        )

    records = _apply_corrections(
        dataset=dataset,
        corrections=review.corrections,
        reviewer=review.reviewer,
    )

    approved_records: list[
        UnifiedDatasetRecord
    ] = []

    for record in records:
        approved_values = {
            key: value.model_copy(
                update={
                    "confidence": max(
                        value.confidence,
                        0.99,
                    ),
                    "requires_human_review":
                        False,
                }
            )
            for key, value
            in record.values.items()
        }

        approved_records.append(
            record.model_copy(
                update={
                    "values":
                        approved_values,
                    "confidence": max(
                        record.confidence,
                        0.99,
                    ),
                    "requires_human_review":
                        False,
                }
            )
        )

    return dataset.model_copy(
        update={
            "records": approved_records,
            "confidence": max(
                dataset.confidence,
                0.99,
            ),
            "requires_human_review":
                False,
            "review_status":
                ReviewStatus.APPROVED,
            "review_reasons": [],
            "reviewed_by":
                review.reviewer,
            "reviewed_at":
                reviewed_at,
            "review_notes":
                review.notes,
        }
    )