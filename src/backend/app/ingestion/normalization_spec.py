"""Versioned JSON contract for deterministic Excel normalization plans."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


NORMALIZATION_SPEC_VERSION = "1.0"
AUTO_ACCEPT_CONFIDENCE = 0.90
REVIEW_CONFIDENCE = 0.75


class LayoutStrategy(str, Enum):
    FLAT_TABLE = "flat_table"
    MULTI_ROW_HEADER = "multi_row_header"
    CROSS_TAB = "cross_tab"
    PERIOD_AXIS = "period_axis"
    MARKER_VALUE_PAIR = "marker_value_pair"


class SourceCoordinateSpec(BaseModel):
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    cell: str


class TableRegionSpec(BaseModel):
    region_id: str
    min_row: int = Field(ge=1)
    max_row: int = Field(ge=1)
    min_column: int = Field(ge=1)
    max_column: int = Field(ge=1)
    discovery_method: str

    @model_validator(mode="after")
    def validate_bounds(self) -> "TableRegionSpec":
        if self.max_row < self.min_row:
            raise ValueError("max_row must be greater than or equal to min_row")
        if self.max_column < self.min_column:
            raise ValueError(
                "max_column must be greater than or equal to min_column"
            )
        return self


class NormalizedMetadataSpec(BaseModel):
    title: str | None = None
    unit: str | None = None
    notes: list[str] = Field(default_factory=list)


class OutputColumnSpec(BaseModel):
    label: str
    source_columns: list[int] = Field(min_length=1)
    header_sources: list[SourceCoordinateSpec] = Field(
        default_factory=list
    )
    selection_rule: Literal[
        "first_non_empty",
        "first_numeric",
    ] = "first_non_empty"
    semantic_role: Literal[
        "dimension",
        "measure",
        "period_measure",
    ] = "measure"
    unit: str | None = None


class PeriodValueGroup(BaseModel):
    label: str
    candidate_columns: list[int] = Field(min_length=1)
    header_source: SourceCoordinateSpec
    unit: str | None = None


class NormalizationSpec(BaseModel):
    """Serializable plan consumed by the existing extraction stage."""

    contract_version: Literal["1.0"] = NORMALIZATION_SPEC_VERSION
    sheet_name: str
    region: TableRegionSpec
    strategy: LayoutStrategy
    header_rows: list[int] = Field(min_length=1)
    data_rows: list[int] = Field(default_factory=list)
    output_columns: list[OutputColumnSpec] = Field(min_length=1)
    period_value_groups: list[PeriodValueGroup] = Field(
        default_factory=list
    )
    metadata: NormalizedMetadataSpec = Field(
        default_factory=NormalizedMetadataSpec
    )
    transformations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_reasons: list[str] = Field(default_factory=list)
    validation_issues: list[str] = Field(default_factory=list)
    explained_numeric_ratio: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )
    invariants_passed: bool = True
    warnings: list[str] = Field(default_factory=list)

    @property
    def requires_human_review(self) -> bool:
        return self.confidence < AUTO_ACCEPT_CONFIDENCE

    @property
    def is_extractable(self) -> bool:
        return self.confidence >= REVIEW_CONFIDENCE

    @model_validator(mode="after")
    def validate_coordinates(self) -> "NormalizationSpec":
        if any(
            row < self.region.min_row or row > self.region.max_row
            for row in self.header_rows + self.data_rows
        ):
            raise ValueError("header_rows and data_rows must be inside region")
        for column in self.output_columns:
            if any(
                source < self.region.min_column
                or source > self.region.max_column
                for source in column.source_columns
            ):
                raise ValueError("source_columns must be inside region")
        return self
