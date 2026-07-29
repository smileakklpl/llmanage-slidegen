from pathlib import Path

from openpyxl import Workbook

from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.schemas import (
    ContainerType,
    PipelineStageStatus,
    PipelineStatus,
    QualityStatus,
)


def test_clean_excel_pipeline(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.xlsx"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "銷售資料"

    worksheet.append([
        "月份",
        "營收",
        "訂單數",
    ])
    worksheet.append([
        "2026-01",
        100000,
        20,
    ])
    worksheet.append([
        "2026-02",
        120000,
        25,
    ])
    worksheet.append([
        "2026-03",
        135000,
        27,
    ])

    workbook.save(file_path)
    workbook.close()

    result = run_ingestion_pipeline(
        file_path
    )

    assert (
        result.pipeline_status
        == PipelineStatus.COMPLETED
    )

    assert result.inspection is not None

    assert (
        result.inspection.detected_container_type
        == ContainerType.XLSX
    )

    assert result.classification is not None
    assert result.extraction is not None
    assert result.validation is not None

    assert result.extraction.table_count == 1

    assert (
        result.validation.status
        == QualityStatus.PASS
    )

    stage_names = {
        stage.stage
        for stage in result.stages
    }

    assert {
        "file_inspection",
        "security_validation",
        "content_classification",
        "table_extraction",
        "data_validation",
        "dataset_normalization",
    }.issubset(stage_names)

    assert all(
        stage.status
        == PipelineStageStatus.COMPLETED
        for stage in result.stages
    )


def test_pipeline_with_warning(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "duplicate_rows.xlsx"

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "資料"

    worksheet.append([
        "名稱",
        "數值",
    ])
    worksheet.append([
        "A",
        100,
    ])
    worksheet.append([
        "A",
        100,
    ])
    worksheet.append([
        "A",
        100,
    ])

    workbook.save(file_path)
    workbook.close()

    result = run_ingestion_pipeline(
        file_path
    )

    assert result.inspection is not None
    assert result.classification is not None
    assert result.extraction is not None
    assert result.validation is not None

    assert result.extraction.table_count == 1

    assert (
        result.pipeline_status
        == PipelineStatus.COMPLETED_WITH_WARNINGS
    )