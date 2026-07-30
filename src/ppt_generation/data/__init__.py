"""
資料讀取與指標計算（Stage 1-3）
================================
把 `src/backend` ingestion 管線的 JSON 輸出，轉成系統唯一真相來源 MetricStore。

- :mod:`backend_bridge` ── 呼叫 backend ingestion 讀 Excel → 該 JSON（兩種版型）
- :mod:`dataset_loader` ── 讀 backend JSON → DataFrame + 每格來源證據
- :mod:`metric_engine`  ── 確定性指標計算，**系統中唯一產生數字的地方**
- :mod:`metric_store`   ── MetricStore：對 LLM 只給 metadata，對本地程式提供查表
"""

from __future__ import annotations

from .backend_bridge import (
    BackendUnavailableError,
    NoExcelInputError,
    ingest_excel,
)
from .dataset_loader import (
    ColumnMeta,
    IngestionPayloadError,
    LoadResult,
    LoadedDataset,
    load_ingestion_file,
    load_ingestion_result,
)
from .metric_engine import (
    EngineConfig,
    EngineReport,
    build_metric_store,
    detect_axis_kind,
)
from .metric_store import (
    MetricNotComputableError,
    MetricNotFoundError,
    MetricSeries,
    MetricStore,
    SourceRef,
)

__all__ = [
    "BackendUnavailableError",
    "ColumnMeta",
    "EngineConfig",
    "EngineReport",
    "IngestionPayloadError",
    "LoadResult",
    "LoadedDataset",
    "MetricNotComputableError",
    "MetricNotFoundError",
    "MetricSeries",
    "MetricStore",
    "NoExcelInputError",
    "SourceRef",
    "build_metric_store",
    "detect_axis_kind",
    "ingest_excel",
    "load_ingestion_file",
    "load_ingestion_result",
]
