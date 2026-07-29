from pathlib import Path

from app.ingestion.delimited import (
    extract_delimited_table,
    inspect_delimited_content,
)
from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.schemas import (
    ColumnDataType,
    PipelineStatus,
    QualityStatus,
    SheetContentType,
)


def test_utf8_csv_classification(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.csv"

    file_path.write_text(
        "月份,營收,訂單數\n"
        "2026-01,100000,20\n"
        "2026-02,120000,25\n"
        "2026-03,135000,27\n",
        encoding="utf-8-sig",
    )

    result = inspect_delimited_content(
        file_path
    )

    assert result.sheet_count == 1

    assert (
        result.overall_content_type
        == SheetContentType.STRUCTURED_TABLE
    )

    assert (
        result.sheets[0].detected_header_row
        == 1
    )


def test_extract_utf8_csv(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.csv"

    file_path.write_text(
        "月份,營收,訂單數\n"
        "2026-01,100000,20\n"
        "2026-02,120000,25\n"
        "2026-03,135000,27\n",
        encoding="utf-8-sig",
    )

    result = extract_delimited_table(
        file_path
    )

    assert result.table_count == 1

    table = result.tables[0]

    assert table.sheet_name == "data"
    assert table.header_row == 1
    assert table.row_count == 3
    assert table.column_count == 3

    assert (
        table.columns[1].data_type
        == ColumnDataType.INTEGER
    )

    assert (
        table.rows[0]
        .cells["營收"]
        .value
        == 100000
    )


def test_tab_delimited_txt(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.txt"

    file_path.write_text(
        "月份\t營收\t訂單數\n"
        "2026-01\t100000\t20\n"
        "2026-02\t120000\t25\n"
        "2026-03\t135000\t27\n",
        encoding="utf-8",
    )

    result = extract_delimited_table(
        file_path
    )

    assert result.table_count == 1
    assert result.tables[0].row_count == 3


def test_pipe_delimited_txt(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "products.txt"

    file_path.write_text(
        "產品|價格|數量\n"
        "A|100|3\n"
        "B|200|2\n"
        "C|150|4\n",
        encoding="utf-8",
    )

    result = extract_delimited_table(
        file_path
    )

    assert result.table_count == 1
    assert result.tables[0].column_count == 3


def test_cp950_csv(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "traditional.csv"

    content = (
        "名稱,數值\n"
        "甲,100\n"
        "乙,200\n"
        "丙,300\n"
    )

    file_path.write_bytes(
        content.encode("cp950")
    )

    result = extract_delimited_table(
        file_path
    )

    assert result.table_count == 1

    assert (
        result.tables[0]
        .rows[0]
        .cells["名稱"]
        .value
        == "甲"
    )


def test_csv_pipeline(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.csv"

    file_path.write_text(
        "月份,營收,訂單數\n"
        "2026-01,100000,20\n"
        "2026-02,120000,25\n"
        "2026-03,135000,27\n",
        encoding="utf-8-sig",
    )

    result = run_ingestion_pipeline(
        file_path
    )

    assert result.inspection is not None
    assert result.classification is not None
    assert result.extraction is not None
    assert result.validation is not None

    assert result.extraction.table_count == 1

    assert (
        result.validation.status
        == QualityStatus.PASS
    )

    assert result.pipeline_status in {
        PipelineStatus.COMPLETED,
        PipelineStatus.COMPLETED_WITH_WARNINGS,
    }