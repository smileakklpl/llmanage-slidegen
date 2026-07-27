import re
from collections import Counter
from datetime import date, datetime
from typing import Any

from app.ingestion.schemas import (
    ColumnDataType,
    ColumnQualitySummary,
    FinancialStatementSubtype,
    QualityIssue,
    QualitySeverity,
    QualityStatus,
    TableDatasetSpec,
    TableQualityReport,
    WorkbookQualityReport,
    WorkbookTableExtraction,
)


HIGH_MISSING_RATE = 0.50
MEDIUM_MISSING_RATE = 0.20

BALANCE_SHEET_TOLERANCE_RATE = 0.001


def _is_missing(value: Any) -> bool:
    """判斷值是否為空。"""
    return value is None or (
        isinstance(value, str)
        and not value.strip()
    )


def _normalize_label(value: Any) -> str:
    """
    正規化財務科目與欄位名稱。

    例如：
    負債及 權益總計
    → 負債及權益總計
    """
    if value is None:
        return ""

    return re.sub(
        r"[\s\u3000:：\-_/()（）]+",
        "",
        str(value).strip().lower(),
    )


def _value_signature(value: Any) -> str:
    """
    將資料轉成可比較的穩定格式，
    用來偵測重複資料列。
    """
    if isinstance(value, (date, datetime)):
        return value.isoformat()

    return repr(value)


def _is_numeric(value: Any) -> bool:
    return isinstance(
        value,
        (int, float),
    ) and not isinstance(value, bool)


def _is_value_compatible(
    value: Any,
    expected_type: ColumnDataType,
) -> bool:
    """
    判斷實際值是否符合欄位推斷型態。
    """
    if _is_missing(value):
        return True

    if expected_type in {
        ColumnDataType.MIXED,
        ColumnDataType.EMPTY,
    }:
        return True

    if expected_type == ColumnDataType.STRING:
        return isinstance(value, str)

    if expected_type == ColumnDataType.INTEGER:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
        )

    if expected_type == ColumnDataType.NUMBER:
        return _is_numeric(value)

    if expected_type == ColumnDataType.BOOLEAN:
        return isinstance(value, bool)

    if expected_type == ColumnDataType.DATE:
        return (
            isinstance(value, date)
            and not isinstance(value, datetime)
        )

    if expected_type == ColumnDataType.DATETIME:
        return isinstance(value, datetime)

    return True


def _calculate_status(
    issues: list[QualityIssue],
) -> QualityStatus:
    if any(
        issue.severity == QualitySeverity.ERROR
        for issue in issues
    ):
        return QualityStatus.FAIL

    if any(
        issue.severity == QualitySeverity.WARNING
        for issue in issues
    ):
        return QualityStatus.WARNING

    return QualityStatus.PASS


def _calculate_score(
    issues: list[QualityIssue],
) -> float:
    """
    簡單品質分數。

    error   每個扣 20 分
    warning 每個扣 5 分
    info    每個扣 1 分
    """
    score = 100.0

    for issue in issues:
        if issue.severity == QualitySeverity.ERROR:
            score -= 20

        elif (
            issue.severity
            == QualitySeverity.WARNING
        ):
            score -= 5

        else:
            score -= 1

    return max(0.0, round(score, 2))


def _validate_table_structure(
    table: TableDatasetSpec,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    if table.column_count == 0:
        issues.append(
            QualityIssue(
                code="NO_COLUMNS",
                severity=QualitySeverity.ERROR,
                message="表格沒有任何欄位",
                sheet_name=table.sheet_name,
            )
        )

    if table.row_count == 0:
        issues.append(
            QualityIssue(
                code="NO_DATA_ROWS",
                severity=QualitySeverity.ERROR,
                message="表格只有表頭，沒有資料列",
                sheet_name=table.sheet_name,
            )
        )

    label_counts = Counter(
        column.label.strip().lower()
        for column in table.columns
    )

    duplicate_labels = [
        label
        for label, count in label_counts.items()
        if count > 1
    ]

    if duplicate_labels:
        issues.append(
            QualityIssue(
                code="DUPLICATE_COLUMN_LABELS",
                severity=QualitySeverity.WARNING,
                message="表格包含重複欄位名稱",
                sheet_name=table.sheet_name,
                details={
                    "duplicate_labels":
                        duplicate_labels
                },
            )
        )

    unnamed_columns = [
        column.label
        for column in table.columns
        if column.label.startswith(
            "未命名欄位"
        )
    ]

    if unnamed_columns:
        issues.append(
            QualityIssue(
                code="UNNAMED_COLUMNS",
                severity=QualitySeverity.WARNING,
                message="部分欄位沒有明確名稱",
                sheet_name=table.sheet_name,
                details={
                    "columns": unnamed_columns
                },
            )
        )

    return issues


def _validate_columns(
    table: TableDatasetSpec,
) -> tuple[
    list[ColumnQualitySummary],
    list[QualityIssue],
]:
    summaries: list[ColumnQualitySummary] = []
    issues: list[QualityIssue] = []

    for column in table.columns:
        values = []
        missing_cells: list[str] = []
        invalid_type_cells: list[str] = []
        formula_without_value_cells: list[str] = []
        number_formats: list[str] = []

        for row in table.rows:
            cell = row.cells.get(column.key)

            if cell is None:
                values.append(None)
                continue

            value = cell.value
            values.append(value)

            if cell.number_format:
                number_formats.append(
                    cell.number_format
                )

            if _is_missing(value):
                missing_cells.append(
                    cell.source.cell
                )

                if cell.formula:
                    formula_without_value_cells.append(
                        cell.source.cell
                    )

                continue

            if not _is_value_compatible(
                value,
                column.data_type,
            ):
                invalid_type_cells.append(
                    cell.source.cell
                )

        missing_count = len(missing_cells)

        if table.row_count:
            missing_rate = (
                missing_count
                / table.row_count
            )
        else:
            missing_rate = 0.0

        non_missing_values = [
            value
            for value in values
            if not _is_missing(value)
        ]

        unique_values = {
            _value_signature(value)
            for value in non_missing_values
        }

        summaries.append(
            ColumnQualitySummary(
                key=column.key,
                label=column.label,
                row_count=table.row_count,
                missing_count=missing_count,
                missing_rate=round(
                    missing_rate,
                    4,
                ),
                invalid_type_count=len(
                    invalid_type_cells
                ),
                unique_count=len(
                    unique_values
                ),
                expected_data_type=(
                    column.data_type
                ),
            )
        )

        if missing_count == table.row_count:
            issues.append(
                QualityIssue(
                    code="EMPTY_COLUMN",
                    severity=QualitySeverity.ERROR,
                    message=(
                        f"欄位「{column.label}」"
                        "完全沒有資料"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    cells=missing_cells[:20],
                )
            )

        elif missing_rate >= HIGH_MISSING_RATE:
            issues.append(
                QualityIssue(
                    code="HIGH_MISSING_RATE",
                    severity=QualitySeverity.WARNING,
                    message=(
                        f"欄位「{column.label}」"
                        f"缺失率為 {missing_rate:.1%}"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    cells=missing_cells[:20],
                    details={
                        "missing_rate":
                            missing_rate
                    },
                )
            )

        elif missing_rate >= MEDIUM_MISSING_RATE:
            issues.append(
                QualityIssue(
                    code="MEDIUM_MISSING_RATE",
                    severity=QualitySeverity.INFO,
                    message=(
                        f"欄位「{column.label}」"
                        f"缺失率為 {missing_rate:.1%}"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    cells=missing_cells[:20],
                )
            )

        if invalid_type_cells:
            issues.append(
                QualityIssue(
                    code="COLUMN_TYPE_MISMATCH",
                    severity=QualitySeverity.ERROR,
                    message=(
                        f"欄位「{column.label}」"
                        "包含不符合推斷型態的資料"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    cells=invalid_type_cells[:20],
                    details={
                        "expected_type":
                            column.data_type.value,
                        "invalid_count":
                            len(invalid_type_cells),
                    },
                )
            )

        if formula_without_value_cells:
            issues.append(
                QualityIssue(
                    code="FORMULA_RESULT_MISSING",
                    severity=QualitySeverity.WARNING,
                    message=(
                        f"欄位「{column.label}」"
                        "有公式，但檔案沒有儲存公式結果"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    cells=(
                        formula_without_value_cells[
                            :20
                        ]
                    ),
                    details={
                        "suggestion": (
                            "使用 Excel 開啟並重新儲存，"
                            "讓公式結果寫入檔案"
                        )
                    },
                )
            )

        if (
            len(non_missing_values) > 1
            and len(unique_values) == 1
        ):
            issues.append(
                QualityIssue(
                    code="CONSTANT_COLUMN",
                    severity=QualitySeverity.INFO,
                    message=(
                        f"欄位「{column.label}」"
                        "所有資料皆相同"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                )
            )

        issues.extend(
            _validate_percentage_column(
                table=table,
                column_key=column.key,
                column_label=column.label,
                column_unit=column.unit,
                values=non_missing_values,
                number_formats=number_formats,
            )
        )

    return summaries, issues


def _validate_percentage_column(
    table: TableDatasetSpec,
    column_key: str,
    column_label: str,
    column_unit: str | None,
    values: list[Any],
    number_formats: list[str],
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    normalized_label = _normalize_label(
        column_label
    )

    percent_like = (
        column_unit == "%"
        or "%" in column_label
        or "比率" in normalized_label
        or "占比" in normalized_label
        or normalized_label.endswith("率")
    )

    if not percent_like:
        return issues

    numeric_values = [
        float(value)
        for value in values
        if _is_numeric(value)
    ]

    if not numeric_values:
        return issues

    has_fraction_values = any(
        0 < abs(value) <= 1
        for value in numeric_values
    )

    has_percentage_point_values = any(
        abs(value) > 1
        for value in numeric_values
    )

    has_excel_percent_format = any(
        "%" in number_format
        for number_format in number_formats
    )

    if (
        has_fraction_values
        and has_percentage_point_values
    ):
        issues.append(
            QualityIssue(
                code="MIXED_PERCENT_SCALE",
                severity=QualitySeverity.WARNING,
                message=(
                    f"欄位「{column_label}」"
                    "可能混用 0.12 與 12 兩種百分比尺度"
                ),
                sheet_name=table.sheet_name,
                column_key=column_key,
                details={
                    "minimum": min(
                        numeric_values
                    ),
                    "maximum": max(
                        numeric_values
                    ),
                },
            )
        )

    elif (
        has_fraction_values
        and not has_excel_percent_format
        and column_unit == "%"
    ):
        issues.append(
            QualityIssue(
                code="AMBIGUOUS_PERCENT_SCALE",
                severity=QualitySeverity.INFO,
                message=(
                    f"欄位「{column_label}」"
                    "的數值介於 0 與 1，"
                    "請確認是比例值還是百分點"
                ),
                sheet_name=table.sheet_name,
                column_key=column_key,
            )
        )

    return issues


def _validate_duplicate_rows(
    table: TableDatasetSpec,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    signatures: dict[
        tuple[str, ...],
        list[int],
    ] = {}

    column_keys = [
        column.key
        for column in table.columns
    ]

    for row in table.rows:
        signature = tuple(
            _value_signature(
                row.cells[key].value
                if key in row.cells
                else None
            )
            for key in column_keys
        )

        signatures.setdefault(
            signature,
            [],
        ).append(row.excel_row)

    duplicate_groups = [
        rows
        for rows in signatures.values()
        if len(rows) > 1
    ]

    if duplicate_groups:
        duplicate_row_numbers = sorted({
            row_number
            for group in duplicate_groups
            for row_number in group
        })

        issues.append(
            QualityIssue(
                code="DUPLICATE_ROWS",
                severity=QualitySeverity.WARNING,
                message=(
                    "表格包含完全相同的重複資料列"
                ),
                sheet_name=table.sheet_name,
                cells=[
                    str(row_number)
                    for row_number
                    in duplicate_row_numbers[:30]
                ],
                details={
                    "duplicate_groups":
                        duplicate_groups[:10]
                },
            )
        )

    return issues


def _find_financial_row(
    table: TableDatasetSpec,
    aliases: set[str],
):
    """
    依第一欄科目名稱尋找財務報表資料列。
    """
    if not table.columns:
        return None

    label_column_key = table.columns[0].key

    normalized_aliases = {
        _normalize_label(alias)
        for alias in aliases
    }

    for row in table.rows:
        cell = row.cells.get(
            label_column_key
        )

        if cell is None:
            continue

        normalized_value = _normalize_label(
            cell.value
        )

        if normalized_value in normalized_aliases:
            return row

    return None


def _validate_balance_sheet(
    table: TableDatasetSpec,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    asset_row = _find_financial_row(
        table,
        {
            "資產總計",
            "資產總額",
            "total assets",
        },
    )

    liability_row = _find_financial_row(
        table,
        {
            "負債總計",
            "負債總額",
            "total liabilities",
        },
    )

    equity_row = _find_financial_row(
        table,
        {
            "權益總計",
            "權益總額",
            "total equity",
        },
    )

    combined_row = _find_financial_row(
        table,
        {
            "負債及權益總計",
            "負債與權益總計",
            "負債及股東權益總計",
            "total liabilities and equity",
        },
    )

    if asset_row is None:
        issues.append(
            QualityIssue(
                code="ASSET_TOTAL_NOT_FOUND",
                severity=QualitySeverity.WARNING,
                message=(
                    "找不到資產總計，"
                    "無法執行資產負債平衡檢查"
                ),
                sheet_name=table.sheet_name,
            )
        )

        return issues

    numeric_columns = [
        column
        for column in table.columns[1:]
        if column.data_type in {
            ColumnDataType.INTEGER,
            ColumnDataType.NUMBER,
        }
    ]

    if not numeric_columns:
        issues.append(
            QualityIssue(
                code="NO_FINANCIAL_VALUE_COLUMNS",
                severity=QualitySeverity.ERROR,
                message=(
                    "資產負債表沒有可驗證的數值欄"
                ),
                sheet_name=table.sheet_name,
            )
        )

        return issues

    for column in numeric_columns:
        asset_value = (
            asset_row.cells[
                column.key
            ].value
        )

        if not _is_numeric(asset_value):
            continue

        expected_value: float | None = None
        comparison_source = ""

        if combined_row is not None:
            combined_value = (
                combined_row.cells[
                    column.key
                ].value
            )

            if _is_numeric(combined_value):
                expected_value = float(
                    combined_value
                )

                comparison_source = (
                    "負債及權益總計"
                )

        if (
            expected_value is None
            and liability_row is not None
            and equity_row is not None
        ):
            liability_value = (
                liability_row.cells[
                    column.key
                ].value
            )

            equity_value = (
                equity_row.cells[
                    column.key
                ].value
            )

            if (
                _is_numeric(liability_value)
                and _is_numeric(equity_value)
            ):
                expected_value = (
                    float(liability_value)
                    + float(equity_value)
                )

                comparison_source = (
                    "負債總計＋權益總計"
                )

        if expected_value is None:
            issues.append(
                QualityIssue(
                    code="BALANCE_CHECK_SKIPPED",
                    severity=QualitySeverity.WARNING,
                    message=(
                        f"欄位「{column.label}」"
                        "缺少負債或權益總計，"
                        "無法驗證會計恆等式"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                )
            )

            continue

        asset_numeric = float(asset_value)

        difference = (
            asset_numeric
            - expected_value
        )

        tolerance = max(
            1.0,
            abs(asset_numeric)
            * BALANCE_SHEET_TOLERANCE_RATE,
        )

        if abs(difference) > tolerance:
            issues.append(
                QualityIssue(
                    code="BALANCE_SHEET_UNBALANCED",
                    severity=QualitySeverity.ERROR,
                    message=(
                        f"欄位「{column.label}」"
                        "的資產總計不等於"
                        f"{comparison_source}"
                    ),
                    sheet_name=table.sheet_name,
                    column_key=column.key,
                    details={
                        "asset_total":
                            asset_numeric,
                        "expected_total":
                            expected_value,
                        "difference":
                            difference,
                        "tolerance":
                            tolerance,
                    },
                )
            )

    return issues


def _validate_financial_statement(
    table: TableDatasetSpec,
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    if table.metadata.unit is None:
        issues.append(
            QualityIssue(
                code="FINANCIAL_UNIT_MISSING",
                severity=QualitySeverity.WARNING,
                message=(
                    "財務報表沒有辨識到幣別或單位"
                ),
                sheet_name=table.sheet_name,
            )
        )

    if (
        table.financial_statement_subtype
        == FinancialStatementSubtype.BALANCE_SHEET
    ):
        issues.extend(
            _validate_balance_sheet(table)
        )

    return issues


def validate_table(
    table: TableDatasetSpec,
) -> TableQualityReport:
    """驗證單張抽取後的表格。"""
    issues: list[QualityIssue] = []

    issues.extend(
        _validate_table_structure(table)
    )

    column_summaries, column_issues = (
        _validate_columns(table)
    )

    issues.extend(column_issues)

    issues.extend(
        _validate_duplicate_rows(table)
    )

    if (
        table.financial_statement_subtype
        is not None
    ):
        issues.extend(
            _validate_financial_statement(
                table
            )
        )

    status = _calculate_status(issues)
    score = _calculate_score(issues)

    error_count = sum(
        issue.severity
        == QualitySeverity.ERROR
        for issue in issues
    )

    warning_count = sum(
        issue.severity
        == QualitySeverity.WARNING
        for issue in issues
    )

    info_count = sum(
        issue.severity
        == QualitySeverity.INFO
        for issue in issues
    )

    return TableQualityReport(
        sheet_name=table.sheet_name,
        status=status,
        score=score,
        row_count=table.row_count,
        column_count=table.column_count,
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        columns=column_summaries,
        issues=issues,
    )


def validate_workbook_extraction(
    extraction: WorkbookTableExtraction,
) -> WorkbookQualityReport:
    """驗證整份 Excel 抽取結果。"""
    table_reports = [
        validate_table(table)
        for table in extraction.tables
    ]

    all_issues = [
        issue
        for report in table_reports
        for issue in report.issues
    ]

    status = _calculate_status(all_issues)

    if table_reports:
        score = sum(
            report.score
            for report in table_reports
        ) / len(table_reports)
    else:
        score = 0.0
        status = QualityStatus.FAIL

    error_count = sum(
        report.error_count
        for report in table_reports
    )

    warning_count = sum(
        report.warning_count
        for report in table_reports
    )

    info_count = sum(
        report.info_count
        for report in table_reports
    )

    return WorkbookQualityReport(
        filename=extraction.filename,
        status=status,
        score=round(score, 2),
        table_count=len(table_reports),
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        tables=table_reports,
    )