from pathlib import Path
from zipfile import (
    ZIP_DEFLATED,
    ZipFile,
)

import pytest
from openpyxl import Workbook

from app.ingestion.detector import (
    inspect_file,
)
from app.ingestion.normalizer import (
    build_unified_datasets,
)
from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.schemas import (
    PipelineStatus,
)
from app.ingestion.security import (
    UnsafeArchiveError,
    validate_xlsx_archive,
)


def _create_xlsx(
    file_path: Path,
    value: int,
) -> None:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Sales"

    worksheet.append([
        "月份",
        "營收",
    ])

    worksheet.append([
        "2026-01",
        value,
    ])

    worksheet.append([
        "2026-02",
        120000,
    ])

    worksheet.append([
        "2026-03",
        135000,
    ])

    workbook.save(file_path)
    workbook.close()


def test_rejects_archive_path_traversal(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "unsafe.xlsx"

    with ZipFile(
        file_path,
        "w",
        ZIP_DEFLATED,
    ) as archive:
        archive.writestr(
            "../malicious.txt",
            "unsafe",
        )

    with pytest.raises(
        UnsafeArchiveError
    ):
        validate_xlsx_archive(
            file_path
        )


def test_dataset_id_changes_with_content(
    tmp_path: Path,
) -> None:
    first_directory = tmp_path / "v1"
    second_directory = tmp_path / "v2"

    first_directory.mkdir()
    second_directory.mkdir()

    first_path = (
        first_directory / "sales.xlsx"
    )

    second_path = (
        second_directory / "sales.xlsx"
    )

    _create_xlsx(
        first_path,
        100000,
    )

    _create_xlsx(
        second_path,
        110000,
    )

    first_result = (
        run_ingestion_pipeline(
            first_path
        )
    )

    second_result = (
        run_ingestion_pipeline(
            second_path
        )
    )

    assert first_result.datasets
    assert second_result.datasets

    assert (
        first_result.datasets[0].dataset_id
        != second_result.datasets[0].dataset_id
    )


def test_pipeline_accepts_normal_xlsx(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.xlsx"

    _create_xlsx(
        file_path,
        100000,
    )

    result = run_ingestion_pipeline(
        file_path
    )

    assert result.pipeline_status in {
        PipelineStatus.COMPLETED,
        PipelineStatus
        .COMPLETED_WITH_WARNINGS,
    }

    assert len(result.datasets) == 1


def test_same_content_has_stable_id(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.xlsx"

    _create_xlsx(
        file_path,
        100000,
    )

    first_result = (
        run_ingestion_pipeline(
            file_path
        )
    )

    second_result = (
        run_ingestion_pipeline(
            file_path
        )
    )

    assert (
        first_result.datasets[0].dataset_id
        == second_result.datasets[0].dataset_id
    )