from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import (
    getSampleStyleSheet,
)
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.ingestion.pdf_parser import (
    extract_pdf_tables,
    inspect_pdf_document,
)
from app.ingestion.pipeline import (
    run_ingestion_pipeline,
)
from app.ingestion.schemas import (
    FinancialStatementSubtype,
    PipelineStatus,
    SheetContentType,
)


def _create_text_pdf(
    file_path: Path,
) -> None:
    document = SimpleDocTemplate(
        str(file_path),
        pagesize=A4,
    )

    styles = getSampleStyleSheet()

    document.build([
        Paragraph(
            "Quarterly Business Report",
            styles["Heading1"],
        ),
        Paragraph(
            "Revenue increased during the quarter.",
            styles["BodyText"],
        ),
    ])


def _create_table_pdf(
    file_path: Path,
    financial: bool = False,
) -> None:
    document = SimpleDocTemplate(
        str(file_path),
        pagesize=A4,
    )

    styles = getSampleStyleSheet()

    if financial:
        title = "ABC Company Balance Sheet"

        data = [
            [
                "Account",
                "2025",
                "2026",
            ],
            [
                "Current Assets",
                "500000",
                "550000",
            ],
            [
                "Total Assets",
                "800000",
                "860000",
            ],
            [
                "Total Liabilities",
                "350000",
                "380000",
            ],
            [
                "Total Equity",
                "450000",
                "480000",
            ],
        ]

    else:
        title = "Sales Data"

        data = [
            [
                "Month",
                "Revenue",
                "Orders",
            ],
            [
                "2026-01",
                "100000",
                "20",
            ],
            [
                "2026-02",
                "120000",
                "25",
            ],
            [
                "2026-03",
                "135000",
                "27",
            ],
        ]

    table = Table(data)

    table.setStyle(
        TableStyle([
            (
                "GRID",
                (0, 0),
                (-1, -1),
                1,
                colors.black,
            ),
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.lightgrey,
            ),
        ])
    )

    document.build([
        Paragraph(
            title,
            styles["Heading1"],
        ),
        Spacer(1, 12),
        table,
    ])


def test_text_pdf_classification(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "report.pdf"

    _create_text_pdf(file_path)

    classification, document = (
        inspect_pdf_document(file_path)
    )

    assert document.page_count == 1
    assert document.has_text_layer is True

    assert (
        classification
        .sheets[0]
        .primary_content_type
        == SheetContentType.DOCUMENT_TEXT
    )


def test_pdf_table_extraction(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.pdf"

    _create_table_pdf(file_path)

    extraction = extract_pdf_tables(
        file_path
    )

    assert extraction.table_count >= 1

    table = extraction.tables[0]

    assert table.column_count == 3
    assert table.row_count == 3

    assert (
        table.rows[0]
        .cells["revenue"]
        .value
        == 100000
    )


def test_financial_pdf(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "balance.pdf"

    _create_table_pdf(
        file_path,
        financial=True,
    )

    classification, _ = (
        inspect_pdf_document(file_path)
    )

    assert (
        classification
        .sheets[0]
        .financial_statement_subtype
        == FinancialStatementSubtype
        .BALANCE_SHEET
    )


def test_pdf_pipeline(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sales.pdf"

    _create_table_pdf(file_path)

    result = run_ingestion_pipeline(
        file_path
    )

    assert result.document is not None
    assert result.extraction is not None

    assert result.extraction.table_count >= 1

    assert result.pipeline_status in {
        PipelineStatus.COMPLETED,
        PipelineStatus
        .COMPLETED_WITH_WARNINGS,
    }