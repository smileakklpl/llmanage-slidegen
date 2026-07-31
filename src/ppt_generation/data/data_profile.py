"""Deterministic, domain-neutral dataset and measure profiling.

The profiler never inspects numeric values to invent business meaning and never
calls an LLM. It uses normalized column metadata, units, and conservative label
rules to decide which mathematical derivations are allowed. Unknown semantics
remain usable as raw values but do not gain additive/share/forecast behavior.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from .dataset_loader import LoadedDataset


ValueSemantic = Literal[
    "count",
    "currency",
    "rate",
    "score",
    "price",
    "duration",
    "index",
    "unknown",
]
AggregationSemantic = Literal["sum", "average", "latest", "none"]
ShapeKind = Literal[
    "entity_by_period",
    "temporal_long",
    "categorical_long",
    "multi_dimension",
]

_PERIOD_PATTERNS = (
    re.compile(r"^\d{3}(?:0[1-9]|1[0-2])$"),
    re.compile(r"^\d{4}(?:0[1-9]|1[0-2])$"),
    re.compile(r"^\d{4}[-/]?(?:0[1-9]|1[0-2])$"),
    re.compile(r"^\d{3,4}年?$"),
    re.compile(r"^(?:[1-9]|1[0-2])月$"),
)


class MeasureProfile(BaseModel):
    key: str
    label: str
    unit: str | None = None
    value_semantic: ValueSemantic = "unknown"
    aggregation_semantic: AggregationSemantic = "none"
    allowed_derivations: list[str] = Field(default_factory=list)
    inference_reason: str


class DatasetProfile(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    dataset_id: str
    shape_kind: ShapeKind
    dimension_keys: list[str]
    measures: list[MeasureProfile]
    warnings: list[str] = Field(default_factory=list)


def _contains(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _looks_period(label: str) -> bool:
    text = str(label).strip()
    return any(pattern.match(text) for pattern in _PERIOD_PATTERNS)


def infer_measure_profile(
    *, key: str, label: str, unit: str | None, dataset_name: str = ""
) -> MeasureProfile:
    """Infer conservative mathematical semantics from metadata only."""
    text = " ".join(part for part in (label, unit or "") if part)

    if _contains(
        text,
        (
            "評分",
            "滿意度",
            "評價",
            "rating",
            "score",
            "stars",
            "星等",
        ),
    ):
        semantic: ValueSemantic = "score"
        aggregation: AggregationSemantic = "average"
        allowed = ["rank"]
        reason = "欄名表示評分／滿意度；可比較排名但不可加總或計算占比"
    elif _contains(
        text,
        (
            "收盤",
            "開盤",
            "股價",
            "單價",
            "票價",
            "房價",
            "price",
            "close",
            "open",
            "high",
            "low",
        ),
    ):
        semantic = "price"
        aggregation = "latest"
        allowed = ["period_growth", "rank", "forecast"]
        reason = "欄名表示價格；可計算變動或排名，不可加總為占比"
    elif _contains(text, ("%", "比率", "比例", "率", "ratio", "rate", "percent")):
        semantic = "rate"
        aggregation = "average"
        allowed = ["period_growth", "rank"]
        reason = "欄名或單位表示比率；不可再次加總為占比"
    elif _contains(
        text,
        (
            "營收",
            "收入",
            "金額",
            "餘額",
            "支出",
            "成本",
            "消費",
            "銷售額",
            "revenue",
            "amount",
            "sales",
            "spend",
            "元",
            "美元",
            "usd",
            "twd",
        ),
    ):
        semantic = "currency"
        aggregation = "sum"
        allowed = ["period_growth", "yoy", "share", "rank", "forecast", "top"]
        reason = "欄名或單位表示可加總金額"
    elif _contains(
        text,
        (
            "訂單",
            "人次",
            "旅客",
            "訪客",
            "數量",
            "卡數",
            "成交量",
            "volume",
            "count",
            "visitors",
            "orders",
            "人",
            "張",
            "筆",
            "件",
        ),
    ):
        semantic = "count"
        aggregation = "sum"
        allowed = ["period_growth", "yoy", "share", "rank", "forecast", "top"]
        reason = "欄名或單位表示可加總數量"
    elif _contains(text, ("時長", "時間", "duration", "分鐘", "小時")):
        semantic = "duration"
        aggregation = "average"
        allowed = ["period_growth", "rank"]
        reason = "欄名或單位表示時間長度；預設以平均值解讀"
    elif _contains(text, ("指數", "index")):
        semantic = "index"
        aggregation = "latest"
        allowed = ["period_growth", "rank", "forecast"]
        reason = "欄名表示指數；可觀察變動但不可計算占比"
    else:
        semantic = "unknown"
        aggregation = "none"
        allowed = ["rank"]
        reason = "無法由 metadata 確認聚合語意；保留原始值並停用加總型推導"

    return MeasureProfile(
        key=key,
        label=label,
        unit=unit,
        value_semantic=semantic,
        aggregation_semantic=aggregation,
        allowed_derivations=allowed,
        inference_reason=reason,
    )


def profile_dataset(dataset: LoadedDataset) -> DatasetProfile:
    numeric = sorted(dataset.numeric_columns(), key=lambda item: item.index)
    dimensions = sorted(dataset.label_columns(), key=lambda item: item.index)
    period_wide = len(numeric) >= 2 and all(
        _looks_period(column.label) for column in numeric
    )

    if period_wide:
        units = {column.unit for column in numeric if column.unit}
        profile = infer_measure_profile(
            key=dataset.dataset_id,
            label=dataset.name,
            unit=next(iter(units)) if len(units) == 1 else None,
            dataset_name=dataset.name,
        )
        measures = [profile]
        shape: ShapeKind = "entity_by_period"
    else:
        measures = [
            infer_measure_profile(
                key=column.key,
                label=column.label,
                unit=column.unit,
            )
            for column in numeric
        ]
        if len(dimensions) > 1:
            shape = "multi_dimension"
        elif dimensions and dimensions[0].is_temporal:
            shape = "temporal_long"
        elif dimensions and any(
            _looks_period(value)
            for value in dataset.frame[dimensions[0].key].dropna().astype(str).tolist()
        ):
            shape = "temporal_long"
        else:
            shape = "categorical_long"

    warnings = [
        f"指標 {item.label} 的聚合語意不明，僅提供保守推導"
        for item in measures
        if item.value_semantic == "unknown"
    ]
    return DatasetProfile(
        dataset_id=dataset.dataset_id,
        shape_kind=shape,
        dimension_keys=[column.key for column in dimensions],
        measures=measures,
        warnings=warnings,
    )
