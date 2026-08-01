"""Deterministic invariants for optimized Excel normalization plans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.ingestion.cell_tokenizer import (
    TokenKind,
    assemble_accounting_values,
    normalize_visible_text,
    parse_accounting_number,
    period_label,
    tokenize_sheet,
)
from app.ingestion.normalization_spec import NormalizationSpec
from app.ingestion.workbook_ir import SheetIR


class InvariantSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class InvariantIssue:
    code: str
    severity: InvariantSeverity
    message: str
    region_id: str


@dataclass(frozen=True)
class PlanValidationReport:
    passed: bool
    issues: tuple[InvariantIssue, ...]


def _regions_overlap(left: NormalizationSpec, right: NormalizationSpec) -> bool:
    return not (
        left.region.max_row < right.region.min_row
        or right.region.max_row < left.region.min_row
        or left.region.max_column < right.region.min_column
        or right.region.max_column < left.region.min_column
    )


def _assembled(
    sheet: SheetIR,
    row: int,
    columns: list[int],
) -> int | float | None:
    return assemble_accounting_values(
        [
            sheet.value_at(
                row,
                column,
                resolve_merged=False,
            )
            for column in columns
        ]
    ).value


def _spec_issues(
    sheet: SheetIR,
    spec: NormalizationSpec,
) -> tuple[list[InvariantIssue], float]:
    issues: list[InvariantIssue] = []
    labels = [column.label for column in spec.output_columns]
    if len(labels) != len(set(labels)):
        issues.append(
            InvariantIssue(
                code="DUPLICATE_OUTPUT_LABEL",
                severity=InvariantSeverity.ERROR,
                message="正規化欄位名稱重複，無法唯一追溯數值",
                region_id=spec.region.region_id,
            )
        )

    measure_columns = [
        column
        for column in spec.output_columns
        if column.semantic_role != "dimension"
    ]
    for column in measure_columns:
        if (
            parse_accounting_number(column.label) is not None
            and period_label(column.label) is None
        ):
            issues.append(
                InvariantIssue(
                    code="DATA_VALUE_USED_AS_HEADER",
                    severity=InvariantSeverity.ERROR,
                    message=f"欄名 {column.label!r} 看起來是資料值",
                    region_id=spec.region.region_id,
                )
            )

    source_sets = [set(column.source_columns) for column in measure_columns]
    for index, left in enumerate(source_sets):
        if any(left & right for right in source_sets[index + 1 :]):
            issues.append(
                InvariantIssue(
                    code="OVERLAPPING_VALUE_GROUPS",
                    severity=InvariantSeverity.ERROR,
                    message="兩個 measure 欄群使用重疊的來源欄",
                    region_id=spec.region.region_id,
                )
            )
            break

    dimension = next(
        (
            column
            for column in spec.output_columns
            if column.semantic_role == "dimension"
        ),
        None,
    )
    if dimension is None:
        issues.append(
            InvariantIssue(
                code="DIMENSION_MISSING",
                severity=InvariantSeverity.WARNING,
                message="表格沒有可追溯的 row label 欄",
                region_id=spec.region.region_id,
            )
        )
    elif spec.data_rows:
        label_coverage = sum(
            bool(
                normalize_visible_text(
                    sheet.value_at(row, dimension.source_columns[0])
                )
            )
            for row in spec.data_rows
        ) / len(spec.data_rows)
        if label_coverage < 0.95:
            issues.append(
                InvariantIssue(
                    code="LABEL_COVERAGE_LOW",
                    severity=InvariantSeverity.WARNING,
                    message=(
                        "資料列的 row label 覆蓋率低於 95%："
                        f"{label_coverage:.0%}"
                    ),
                    region_id=spec.region.region_id,
                )
            )

    tokenized = tokenize_sheet(sheet)
    measure_source_columns = {
        source_column
        for column in measure_columns
        for source_column in column.source_columns
    }
    raw_numeric_tokens = [
        token
        for row in spec.data_rows
        for token in tokenized.row_tokens(row)
        if token.kind
        in {
            TokenKind.NUMBER,
            TokenKind.ACCOUNTING_NUMBER,
            TokenKind.PERIOD,
        }
        and token.min_column in measure_source_columns
    ]
    assigned_values = sum(
        _assembled(sheet, row, column.source_columns) is not None
        for row in spec.data_rows
        for column in measure_columns
    )
    explained_ratio = (
        min(1.0, assigned_values / len(raw_numeric_tokens))
        if raw_numeric_tokens
        else 1.0
    )
    if explained_ratio < 0.85:
        issues.append(
            InvariantIssue(
                code="NUMERIC_COVERAGE_LOW",
                severity=InvariantSeverity.WARNING,
                message=(
                    "正規化 plan 解釋的原始數值比例低於 85%："
                    f"{explained_ratio:.0%}"
                ),
                region_id=spec.region.region_id,
            )
        )

    unresolved_fragments = [
        token.sources[0].cell
        for row in spec.data_rows
        for token in tokenized.row_tokens(row)
        if token.kind == TokenKind.ACCOUNTING_FRAGMENT
        and token.min_column in measure_source_columns
    ]
    if unresolved_fragments:
        issues.append(
            InvariantIssue(
                code="UNRESOLVED_ACCOUNTING_FRAGMENT",
                severity=InvariantSeverity.ERROR,
                message=(
                    "仍有未閉合的會計數字片段："
                    + "、".join(unresolved_fragments[:10])
                ),
                region_id=spec.region.region_id,
            )
        )

    return issues, explained_ratio


def validate_normalization_plan(
    sheet: SheetIR,
    specs: list[NormalizationSpec],
) -> tuple[list[NormalizationSpec], PlanValidationReport]:
    """Validate and confidence-adjust a complete worksheet plan."""
    all_issues: list[InvariantIssue] = []
    overlap_regions: set[str] = set()
    for index, left in enumerate(specs):
        for right in specs[index + 1 :]:
            if _regions_overlap(left, right):
                overlap_regions.update(
                    {
                        left.region.region_id,
                        right.region.region_id,
                    }
                )
                all_issues.append(
                    InvariantIssue(
                        code="OVERLAPPING_TABLE_PLANS",
                        severity=InvariantSeverity.ERROR,
                        message="兩個 logical table plans 的來源範圍重疊",
                        region_id=left.region.region_id,
                    )
                )

    validated: list[NormalizationSpec] = []
    for spec in specs:
        issues, explained_ratio = _spec_issues(sheet, spec)
        if spec.region.region_id in overlap_regions:
            issues.append(
                InvariantIssue(
                    code="PLAN_OVERLAP_MEMBER",
                    severity=InvariantSeverity.ERROR,
                    message="此 plan 與另一個 table plan 重疊",
                    region_id=spec.region.region_id,
                )
            )
        all_issues.extend(issues)
        errors = [
            issue
            for issue in issues
            if issue.severity == InvariantSeverity.ERROR
        ]
        warnings = [
            issue
            for issue in issues
            if issue.severity == InvariantSeverity.WARNING
        ]
        confidence = spec.confidence
        if warnings:
            confidence = min(confidence, 0.89)
        if errors:
            confidence = min(confidence, 0.74)
        issue_messages = [issue.message for issue in issues]
        plan_warnings = list(spec.warnings)
        plan_warnings.extend(
            f"人工確認：{message}"
            for message in issue_messages
        )
        validated.append(
            spec.model_copy(
                update={
                    "confidence": round(confidence, 4),
                    "confidence_reasons": (
                        spec.confidence_reasons
                        + [
                            "數值來源解釋率 "
                            f"{explained_ratio:.0%}"
                        ]
                    ),
                    "warnings": list(
                        dict.fromkeys(plan_warnings)
                    ),
                    "validation_issues": issue_messages,
                    "explained_numeric_ratio": round(
                        explained_ratio,
                        4,
                    ),
                    "invariants_passed": not errors,
                }
            )
        )

    return validated, PlanValidationReport(
        passed=not any(
            issue.severity == InvariantSeverity.ERROR
            for issue in all_issues
        ),
        issues=tuple(all_issues),
    )
