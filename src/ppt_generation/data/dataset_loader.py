"""
backend ingestion JSON 讀取器
==============================
對應 docs/圖表原生性與資料同步設計.md Stage 1 → Stage 2 的交界。

輸入：`src/backend` ingestion 管線輸出的 ``UnifiedIngestionResult`` JSON
輸出：pandas DataFrame + 欄位 metadata + 每格來源證據

設計約束（對齊 steering 的模組邊界原則）：
本模組**不得**直接讀取原始 xlsx/pdf/圖片，只吃 backend 的 JSON 契約。
原始檔案的格式偵測、抽取、品質驗證都是 backend 的責任。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .metric_store import SourceRef


#: 低於此信度的資料集，預設不允許進入指標計算（OCR 誤讀風險）。
MIN_DATASET_CONFIDENCE = 0.70


class IngestionPayloadError(ValueError):
    """backend JSON 格式不符預期。"""


@dataclass
class ColumnMeta:
    """欄位 metadata，承接 backend 的 TableColumnSpec。"""

    key: str
    label: str
    index: int
    data_type: str
    unit: str | None = None

    @property
    def is_numeric(self) -> bool:
        return self.data_type in {"integer", "number"}

    @property
    def is_temporal(self) -> bool:
        return self.data_type in {"date", "datetime"}


@dataclass
class LoadedDataset:
    """
    一個已載入的資料集。

    ``frame`` 的欄位名採用 backend 的 column key（程式友善），
    ``columns`` 保留 label（人類可讀，用於圖表軸標籤與指標名稱）。
    """

    dataset_id: str
    name: str
    filename: str
    source_kind: str
    table_kind: str
    frame: pd.DataFrame
    columns: dict[str, ColumnMeta]
    #: key 為 f"{column_key}|{record_index}"
    evidence: dict[str, SourceRef] = field(default_factory=dict)
    confidence: float = 1.0
    requires_human_review: bool = False
    review_status: str = "not_required"
    warnings: list[str] = field(default_factory=list)

    def column_label(self, column_key: str) -> str:
        meta = self.columns.get(column_key)
        return meta.label if meta else column_key

    def numeric_columns(self) -> list[ColumnMeta]:
        return [meta for meta in self.columns.values() if meta.is_numeric]

    def label_columns(self) -> list[ColumnMeta]:
        """非數值欄位，通常是類別軸（月份、銀行名稱、科目名稱）。"""
        return [meta for meta in self.columns.values() if not meta.is_numeric]

    def source_of(self, column_key: str, record_index: int) -> SourceRef | None:
        return self.evidence.get(f"{column_key}|{record_index}")


@dataclass
class LoadResult:
    """載入結果，含被排除的資料集與原因。"""

    datasets: list[LoadedDataset] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    pipeline_status: str = "unknown"
    source_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def _first_evidence(value_payload: dict[str, Any]) -> SourceRef | None:
    """
    取一筆數值的主要來源證據。

    backend 可能對同一格附多筆證據（例如 OCR 區塊 + 表格範圍），
    取第一筆最細粒度的即可滿足「可回溯至來源儲存格」的要求。
    """
    evidence_list = value_payload.get("evidence") or []

    if not evidence_list:
        return None

    # 優先取 cell 層級的證據，其次才是 range/page 層級。
    for item in evidence_list:
        if item.get("cell"):
            return SourceRef.from_dict(item)

    return SourceRef.from_dict(evidence_list[0])


def _parse_columns(dataset_payload: dict[str, Any]) -> dict[str, ColumnMeta]:
    columns: dict[str, ColumnMeta] = {}

    for column in dataset_payload.get("columns", []):
        try:
            meta = ColumnMeta(
                key=column["key"],
                label=column.get("label") or column["key"],
                index=int(column.get("index", len(columns))),
                data_type=column.get("data_type", "string"),
                unit=column.get("unit"),
            )
        except KeyError as error:
            raise IngestionPayloadError(
                f"資料集欄位缺少必要屬性：{error}"
            ) from error

        columns[meta.key] = meta

    if not columns:
        raise IngestionPayloadError(
            f"資料集 {dataset_payload.get('dataset_id')!r} 沒有欄位定義"
        )

    return columns


def _parse_records(
    dataset_payload: dict[str, Any],
    columns: dict[str, ColumnMeta],
) -> tuple[pd.DataFrame, dict[str, SourceRef]]:
    rows: list[dict[str, Any]] = []
    evidence: dict[str, SourceRef] = {}

    for record in dataset_payload.get("records", []):
        record_index = int(record.get("record_index", len(rows)))
        row: dict[str, Any] = {"__record_index__": record_index}

        for column_key in columns:
            value_payload = (record.get("values") or {}).get(column_key) or {}
            row[column_key] = value_payload.get("value")

            source = _first_evidence(value_payload)

            if source is not None:
                evidence[f"{column_key}|{record_index}"] = source

        rows.append(row)

    frame = pd.DataFrame(rows, columns=["__record_index__", *columns])

    # 數值欄位強制轉數值型別，無法轉換的留 NaN，
    # 後續 metric_engine 會據此判斷資料是否足以計算指標。
    for meta in columns.values():
        if meta.is_numeric:
            frame[meta.key] = pd.to_numeric(frame[meta.key], errors="coerce")

    return frame, evidence


def _parse_dataset(dataset_payload: dict[str, Any]) -> LoadedDataset:
    columns = _parse_columns(dataset_payload)
    frame, evidence = _parse_records(dataset_payload, columns)

    metadata = dataset_payload.get("metadata") or {}

    return LoadedDataset(
        dataset_id=dataset_payload.get("dataset_id", "unknown"),
        name=(
            metadata.get("title")
            or dataset_payload.get("name")
            or dataset_payload.get("dataset_id", "unknown")
        ),
        filename=dataset_payload.get("filename", "unknown"),
        source_kind=dataset_payload.get("source_kind", "unknown"),
        table_kind=dataset_payload.get("table_kind", "unknown"),
        frame=frame,
        columns=columns,
        evidence=evidence,
        confidence=float(dataset_payload.get("confidence", 1.0)),
        requires_human_review=bool(
            dataset_payload.get("requires_human_review", False)
        ),
        review_status=dataset_payload.get("review_status", "not_required"),
        warnings=list(dataset_payload.get("warnings", [])),
    )


def load_ingestion_result(
    payload: dict[str, Any],
    *,
    min_confidence: float = MIN_DATASET_CONFIDENCE,
    allow_pending_review: bool = False,
) -> LoadResult:
    """
    將 backend 的 ``UnifiedIngestionResult`` 轉成可計算的資料集清單。

    Args:
        payload: backend JSON（已 ``json.loads``）。
        min_confidence: 低於此信度的資料集被排除。
        allow_pending_review: 是否允許尚未人工確認的資料集進入計算。
            預設 False —— 低信度 OCR 資料未經確認就進簡報，違反
            「數字 100% 正確且可追溯」的產品原則。

    Returns:
        :class:`LoadResult`，含通過的資料集與被排除者的原因。
    """
    if not isinstance(payload, dict):
        raise IngestionPayloadError("ingestion 結果必須是 JSON object")

    if "datasets" not in payload:
        raise IngestionPayloadError(
            "ingestion 結果缺少 datasets 欄位，"
            "請確認傳入的是 UnifiedIngestionResult"
        )

    result = LoadResult(
        pipeline_status=payload.get("pipeline_status", "unknown"),
        warnings=list(payload.get("warnings", [])),
    )

    if payload.get("pipeline_status") in {"rejected", "failed", "unsupported"}:
        errors = "；".join(payload.get("errors", [])) or "未提供錯誤原因"
        raise IngestionPayloadError(
            f"backend 管線狀態為 {payload['pipeline_status']}，無法生成簡報：{errors}"
        )

    for dataset_payload in payload["datasets"]:
        dataset = _parse_dataset(dataset_payload)

        if dataset.filename not in result.source_files:
            result.source_files.append(dataset.filename)

        if dataset.frame.empty:
            result.skipped[dataset.dataset_id] = "資料集沒有任何資料列"
            continue

        if dataset.confidence < min_confidence:
            result.skipped[dataset.dataset_id] = (
                f"資料集信度 {dataset.confidence:.2f} 低於門檻 {min_confidence:.2f}"
            )
            continue

        if (
            dataset.requires_human_review
            and dataset.review_status != "approved"
            and not allow_pending_review
        ):
            result.skipped[dataset.dataset_id] = (
                f"資料集需人工確認，目前狀態為 {dataset.review_status}"
            )
            continue

        if dataset.review_status == "rejected":
            result.skipped[dataset.dataset_id] = "資料集已被人工審核拒絕"
            continue

        result.datasets.append(dataset)

    if not result.datasets:
        reasons = "；".join(result.skipped.values()) or "backend 未輸出任何資料集"
        raise IngestionPayloadError(f"沒有可用於簡報的資料集：{reasons}")

    return result


def load_ingestion_file(
    path: str | Path,
    **kwargs: Any,
) -> LoadResult:
    """從檔案讀取 backend ingestion JSON。"""
    target = Path(path)

    if not target.exists():
        raise FileNotFoundError(f"找不到 ingestion JSON：{target}")

    payload = json.loads(target.read_text(encoding="utf-8"))
    return load_ingestion_result(payload, **kwargs)
