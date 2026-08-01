"""Worksheet-level grid graph and logical table hypothesis generation."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Literal

from app.ingestion.cell_tokenizer import (
    CellToken,
    TokenKind,
    TokenizedSheet,
    assemble_accounting_values,
    normalize_visible_text,
    tokenize_sheet,
)
from app.ingestion.normalization_spec import (
    LayoutStrategy,
    NormalizationSpec,
    NormalizedMetadataSpec,
    OutputColumnSpec,
    PeriodValueGroup,
    SourceCoordinateSpec,
    TableRegionSpec,
)
from app.ingestion.workbook_ir import SheetIR


@dataclass(frozen=True)
class GridEdge:
    source_cell: str
    target_cell: str
    relation: Literal["same_row", "same_column"]
    distance: int


@dataclass(frozen=True)
class GridRelationshipGraph:
    tokenized: TokenizedSheet
    horizontal_edges: tuple[GridEdge, ...]
    vertical_edges: tuple[GridEdge, ...]


@dataclass(frozen=True)
class GridColumnGroup:
    period: str
    currency: str | None
    min_column: int
    max_column: int
    header_sources: tuple[SourceCoordinateSpec, ...]

    @property
    def label(self) -> str:
        if self.currency and self.currency != "$":
            return f"{self.period} {self.currency}"
        return self.period


@dataclass(frozen=True)
class LogicalTableHypothesis:
    spec: NormalizationSpec
    score: float
    covered_cells: frozenset[str]


def _source(token: CellToken) -> SourceCoordinateSpec:
    source = token.sources[0]
    return SourceCoordinateSpec(
        row=source.row,
        column=source.column,
        cell=source.cell,
    )


def _build_edges(tokenized: TokenizedSheet) -> tuple[
    tuple[GridEdge, ...],
    tuple[GridEdge, ...],
]:
    horizontal: list[GridEdge] = []
    vertical: list[GridEdge] = []

    for tokens in tokenized.by_row.values():
        ordered = sorted(tokens, key=lambda token: token.min_column)
        for left, right in zip(ordered, ordered[1:]):
            horizontal.append(
                GridEdge(
                    source_cell=left.sources[0].cell,
                    target_cell=right.sources[0].cell,
                    relation="same_row",
                    distance=max(0, right.min_column - left.max_column),
                )
            )

    by_column: dict[int, list[CellToken]] = {}
    for token in tokenized.tokens:
        by_column.setdefault(token.min_column, []).append(token)
    for tokens in by_column.values():
        ordered = sorted(tokens, key=lambda token: token.row)
        for upper, lower in zip(ordered, ordered[1:]):
            vertical.append(
                GridEdge(
                    source_cell=upper.sources[0].cell,
                    target_cell=lower.sources[0].cell,
                    relation="same_column",
                    distance=max(0, lower.row - upper.row),
                )
            )

    return tuple(horizontal), tuple(vertical)


def build_grid_relationship_graph(sheet: SheetIR) -> GridRelationshipGraph:
    tokenized = tokenize_sheet(sheet)
    horizontal, vertical = _build_edges(tokenized)
    return GridRelationshipGraph(
        tokenized=tokenized,
        horizontal_edges=horizontal,
        vertical_edges=vertical,
    )


def _period_axes(graph: GridRelationshipGraph) -> list[tuple[int, list[CellToken]]]:
    candidates: list[tuple[int, list[CellToken]]] = []
    for row, tokens in sorted(graph.tokenized.by_row.items()):
        periods = [token for token in tokens if token.kind == TokenKind.PERIOD]
        labels = [str(token.value) for token in periods]
        if len(periods) < 2 or len(set(labels)) != len(labels):
            continue

        # A period axis is header-shaped: periods dominate the visible cells
        # in that row.  Wide record rows can legitimately contain values in
        # the ROC-year range (for example 106 and 110), but a few such values
        # among dozens of dimensions/measures must not turn an interior data
        # row into a worksheet-level period hypothesis.
        period_density = len(periods) / len(tokens)
        if period_density < 0.5:
            continue

        numeric_labels = [
            int(label)
            for label in labels
            if label.isdigit()
        ]
        if (
            len(numeric_labels) == len(labels)
            and max(numeric_labels) - min(numeric_labels) > 10
        ):
            continue
        candidates.append(
            (row, sorted(periods, key=lambda token: token.min_column))
        )

    if not candidates:
        return []
    first_labels = [str(token.value) for token in candidates[0][1]]
    axes = [candidates[0]]
    for row, periods in candidates[1:]:
        labels = [str(token.value) for token in periods]
        if labels == first_labels and row - axes[-1][0] > 3:
            axes.append((row, periods))
    return axes


def _currency_tokens(
    graph: GridRelationshipGraph,
    start_row: int,
    end_row: int,
) -> list[CellToken]:
    return [
        token
        for row in range(start_row, end_row + 1)
        for token in graph.tokenized.row_tokens(row)
        if token.kind == TokenKind.CURRENCY
    ]


def _metadata(
    graph: GridRelationshipGraph,
    header_row: int,
) -> tuple[NormalizedMetadataSpec, list[str]]:
    title: str | None = None
    unit: str | None = None
    notes: list[str] = []
    warnings: list[str] = []

    for row in range(1, header_row):
        texts = [
            token.text
            for token in graph.tokenized.row_tokens(row)
            if token.text
        ]
        if not texts:
            continue
        combined = normalize_visible_text(" ".join(texts))
        lowered = combined.lower()
        if (
            "in thousands" in lowered
            or "單位" in combined.lower()
            or lowered.startswith("unit:")
            or lowered.startswith("unit：")
        ):
            if unit is None:
                if ":" in combined or "：" in combined:
                    unit = combined.replace("：", ":").split(":", 1)[1].strip()
                else:
                    unit = combined.strip("() ")
            if "except" in lowered:
                warnings.append(
                    "人工確認：單位說明包含例外項目，已保留完整單位階層"
                )
            notes.append(combined)
        elif title is None:
            title = combined
        else:
            notes.append(combined)

    return NormalizedMetadataSpec(
        title=title,
        unit=unit,
        notes=notes,
    ), warnings


def _group_unit(currency: str | None, metadata_unit: str | None) -> str | None:
    if currency in {None, "$"}:
        return metadata_unit
    if metadata_unit and "thousand" in metadata_unit.lower():
        return f"{currency} thousands"
    return currency or metadata_unit


def _period_display(token: CellToken) -> str:
    text = normalize_visible_text(token.text)
    if text.endswith(("年", "月")) and any(
        character.isdigit() for character in text
    ):
        return text
    return str(token.value)


def _build_column_groups(
    sheet: SheetIR,
    graph: GridRelationshipGraph,
    header_row: int,
    period_tokens: list[CellToken],
) -> list[GridColumnGroup]:
    anchors = [token.min_column for token in period_tokens]
    gaps = [right - left for left, right in zip(anchors, anchors[1:])]
    typical_gap = max(1, int(round(median(gaps))) if gaps else 1)
    header_currencies = _currency_tokens(
        graph,
        header_row,
        min(sheet.max_row, header_row + 3),
    )
    trailing_currencies = [
        token
        for token in header_currencies
        if token.min_column > anchors[-1]
        and token.value != "$"
    ]
    trailing_starts = sorted(
        {token.min_column for token in trailing_currencies}
    )
    first_trailing = trailing_starts[0] if trailing_starts else None

    groups: list[GridColumnGroup] = []
    for index, period_token in enumerate(period_tokens):
        start = period_token.min_column
        if index + 1 < len(period_tokens):
            end = period_tokens[index + 1].min_column - 1
        elif first_trailing is not None:
            end = first_trailing - 1
        else:
            end = min(sheet.max_column, start + typical_gap - 1)

        currencies = [
            token
            for token in header_currencies
            if start <= token.min_column <= end
        ]
        specific = next(
            (token for token in currencies if token.value != "$"),
            None,
        )
        currency = str(specific.value) if specific is not None else None
        sources = [_source(period_token)]
        if specific is not None:
            sources.append(_source(specific))
        groups.append(
            GridColumnGroup(
                period=_period_display(period_token),
                currency=currency,
                min_column=start,
                max_column=end,
                header_sources=tuple(sources),
            )
        )

    for index, start in enumerate(trailing_starts):
        end = (
            trailing_starts[index + 1] - 1
            if index + 1 < len(trailing_starts)
            else min(sheet.max_column, start + typical_gap - 1)
        )
        currency_token = next(
            token
            for token in trailing_currencies
            if token.min_column == start
        )
        groups.append(
            GridColumnGroup(
                period=_period_display(period_tokens[-1]),
                currency=str(currency_token.value),
                min_column=start,
                max_column=end,
                header_sources=(
                    _source(period_tokens[-1]),
                    _source(currency_token),
                ),
            )
        )

    return groups


def _assembled_value(
    sheet: SheetIR,
    row: int,
    group: GridColumnGroup,
) -> int | float | None:
    assembled = assemble_accounting_values(
        [
            sheet.value_at(
                row,
                column,
                resolve_merged=False,
            )
            for column in range(group.min_column, group.max_column + 1)
        ]
    )
    return assembled.value


def _label_column(
    sheet: SheetIR,
    graph: GridRelationshipGraph,
    header_row: int,
    end_row: int,
    first_value_column: int,
) -> int | None:
    candidates = range(1, first_value_column)
    scores: list[tuple[int, int]] = []
    for column in candidates:
        score = sum(
            1
            for row in range(header_row + 1, end_row + 1)
            if (
                (value := sheet.value_at(row, column)) is not None
                and bool(normalize_visible_text(value))
                and not isinstance(value, (int, float))
            )
        )
        scores.append((score, column))
    if not scores:
        return None
    score, column = max(scores, key=lambda item: (item[0], -item[1]))
    return column if score >= 2 else None


def _hypothesis(
    sheet: SheetIR,
    graph: GridRelationshipGraph,
    header_row: int,
    period_tokens: list[CellToken],
    next_header_row: int | None,
    hypothesis_index: int,
) -> LogicalTableHypothesis | None:
    groups = _build_column_groups(
        sheet,
        graph,
        header_row,
        period_tokens,
    )
    if len(groups) < 2:
        return None

    end_row = (
        next_header_row - 1
        if next_header_row is not None
        else sheet.max_row
    )
    label_column = _label_column(
        sheet,
        graph,
        header_row,
        end_row,
        groups[0].min_column,
    )
    if label_column is None:
        return None

    data_rows = [
        row
        for row in range(header_row + 1, end_row + 1)
        if normalize_visible_text(sheet.value_at(row, label_column))
        and any(
            _assembled_value(sheet, row, group) is not None
            for group in groups
        )
    ]
    if len(data_rows) < 2:
        return None

    metadata, warnings = _metadata(graph, header_row)
    header_label = normalize_visible_text(
        sheet.value_at(header_row, label_column)
    ) or "項目"
    output_columns = [
        OutputColumnSpec(
            label=header_label,
            source_columns=[label_column],
            header_sources=[
                SourceCoordinateSpec(
                    row=header_row,
                    column=label_column,
                    cell=f"{sheet.cell_at(header_row, label_column).coordinate}"
                    if sheet.cell_at(header_row, label_column) is not None
                    else f"A{header_row}",
                )
            ],
            selection_rule="first_non_empty",
            semantic_role="dimension",
        )
    ]
    period_groups: list[PeriodValueGroup] = []
    for group in groups:
        unit = _group_unit(group.currency, metadata.unit)
        output_columns.append(
            OutputColumnSpec(
                label=group.label,
                source_columns=list(
                    range(group.min_column, group.max_column + 1)
                ),
                header_sources=list(group.header_sources),
                selection_rule="first_numeric",
                semantic_role="period_measure",
                unit=unit,
            )
        )
        period_groups.append(
            PeriodValueGroup(
                label=group.label,
                candidate_columns=list(
                    range(group.min_column, group.max_column + 1)
                ),
                header_source=group.header_sources[0],
                unit=unit,
            )
        )

    group_coverages = [
        sum(
            _assembled_value(sheet, row, group) is not None
            for row in data_rows
        )
        / len(data_rows)
        for group in groups
    ]
    coverage = sum(group_coverages) / len(group_coverages)
    score = min(
        0.98,
        0.88
        + min(len(groups), 4) * 0.015
        + coverage * 0.04,
    )
    max_column = max(group.max_column for group in groups)
    header_rows = sorted(
        {
            header_row,
            *(
                source.row
                for group in groups
                for source in group.header_sources
            ),
        }
    )
    strategy = (
        LayoutStrategy.PERIOD_AXIS
        if all(
            group.min_column == group.max_column
            and group.currency in {None, "$"}
            for group in groups
        )
        else LayoutStrategy.MARKER_VALUE_PAIR
    )
    spec = NormalizationSpec(
        sheet_name=sheet.title,
        region=TableRegionSpec(
            region_id=f"grid_graph_{hypothesis_index}",
            min_row=header_row,
            max_row=max(data_rows),
            min_column=label_column,
            max_column=max_column,
            discovery_method="grid_relationship_graph",
        ),
        strategy=strategy,
        header_rows=header_rows,
        data_rows=data_rows,
        output_columns=output_columns,
        period_value_groups=period_groups,
        metadata=metadata,
        transformations=[
            "grid_graph_grouping",
            "period_currency_axis_to_columns",
            "accounting_fragment_assembly",
        ],
        confidence=round(score, 4),
        confidence_reasons=[
            f"全表辨識到 {len(groups)} 個期間/幣別欄群",
            f"欄群數值覆蓋率 {coverage:.0%}",
            f"保留 {len(data_rows)} 個具有 row label 的資料列",
        ],
        warnings=warnings,
    )

    covered_cells: set[str] = set()
    for row in data_rows:
        label_cell = sheet.cell_at(row, label_column)
        if label_cell is not None:
            covered_cells.add(label_cell.coordinate)
        for group in groups:
            for column in range(group.min_column, group.max_column + 1):
                cell = sheet.cell_at(row, column, resolve_merged=False)
                if cell is not None:
                    covered_cells.add(cell.coordinate)

    return LogicalTableHypothesis(
        spec=spec,
        score=score,
        covered_cells=frozenset(covered_cells),
    )


def _same_column_contract(
    left: LogicalTableHypothesis,
    right: LogicalTableHypothesis,
) -> bool:
    return [
        (column.label, tuple(column.source_columns))
        for column in left.spec.output_columns
    ] == [
        (column.label, tuple(column.source_columns))
        for column in right.spec.output_columns
    ]


def _merge_vertical_continuations(
    hypotheses: list[LogicalTableHypothesis],
) -> list[LogicalTableHypothesis]:
    """Merge repeated page headers that continue the same logical table."""
    merged: list[LogicalTableHypothesis] = []
    for hypothesis in sorted(
        hypotheses,
        key=lambda item: item.spec.region.min_row,
    ):
        if not merged:
            merged.append(hypothesis)
            continue
        previous = merged[-1]
        gap = (
            hypothesis.spec.region.min_row
            - previous.spec.region.max_row
            - 1
        )
        if gap > 10 or not _same_column_contract(previous, hypothesis):
            merged.append(hypothesis)
            continue

        combined_columns: list[OutputColumnSpec] = []
        for left_column, right_column in zip(
            previous.spec.output_columns,
            hypothesis.spec.output_columns,
        ):
            header_sources = list(left_column.header_sources)
            known = {(source.row, source.column) for source in header_sources}
            header_sources.extend(
                source
                for source in right_column.header_sources
                if (source.row, source.column) not in known
            )
            combined_columns.append(
                left_column.model_copy(
                    update={"header_sources": header_sources}
                )
            )

        combined_spec = previous.spec.model_copy(
            update={
                "region": previous.spec.region.model_copy(
                    update={
                        "max_row": hypothesis.spec.region.max_row,
                        "max_column": max(
                            previous.spec.region.max_column,
                            hypothesis.spec.region.max_column,
                        ),
                    }
                ),
                "header_rows": sorted(
                    set(previous.spec.header_rows)
                    | set(hypothesis.spec.header_rows)
                ),
                "data_rows": sorted(
                    set(previous.spec.data_rows)
                    | set(hypothesis.spec.data_rows)
                ),
                "output_columns": combined_columns,
                "transformations": list(
                    dict.fromkeys(
                        previous.spec.transformations
                        + hypothesis.spec.transformations
                        + ["merge_vertical_continuation"]
                    )
                ),
                "confidence": round(
                    min(previous.score, hypothesis.score),
                    4,
                ),
                "confidence_reasons": (
                    previous.spec.confidence_reasons
                    + ["重複欄軸被辨識為垂直續頁並合併"]
                ),
                "warnings": list(
                    dict.fromkeys(
                        previous.spec.warnings
                        + hypothesis.spec.warnings
                    )
                ),
            }
        )
        merged[-1] = LogicalTableHypothesis(
            spec=combined_spec,
            score=min(previous.score, hypothesis.score),
            covered_cells=(
                previous.covered_cells
                | hypothesis.covered_cells
            ),
        )
    return merged


def build_logical_table_hypotheses(
    sheet: SheetIR,
) -> tuple[GridRelationshipGraph, list[LogicalTableHypothesis]]:
    graph = build_grid_relationship_graph(sheet)
    axes = _period_axes(graph)
    hypotheses: list[LogicalTableHypothesis] = []
    for index, (row, periods) in enumerate(axes, start=1):
        next_row = axes[index][0] if index < len(axes) else None
        candidate = _hypothesis(
            sheet,
            graph,
            row,
            periods,
            next_row,
            index,
        )
        if candidate is not None:
            hypotheses.append(candidate)
    return graph, _merge_vertical_continuations(hypotheses)


def _regions_overlap(left: NormalizationSpec, right: NormalizationSpec) -> bool:
    return not (
        left.region.max_row < right.region.min_row
        or right.region.max_row < left.region.min_row
        or left.region.max_column < right.region.min_column
        or right.region.max_column < left.region.min_column
    )


def optimize_logical_plan(
    hypotheses: list[LogicalTableHypothesis],
    regional_specs: list[NormalizationSpec],
) -> list[NormalizationSpec]:
    """Prefer high-coverage worksheet hypotheses, preserving disjoint tables."""
    selected: list[NormalizationSpec] = []
    for hypothesis in sorted(
        hypotheses,
        key=lambda item: item.score,
        reverse=True,
    ):
        if not any(
            _regions_overlap(hypothesis.spec, existing)
            for existing in selected
        ):
            selected.append(hypothesis.spec)

    for spec in regional_specs:
        if not any(_regions_overlap(spec, existing) for existing in selected):
            selected.append(spec)

    return sorted(
        selected,
        key=lambda spec: (
            spec.region.min_row,
            spec.region.min_column,
        ),
    )
