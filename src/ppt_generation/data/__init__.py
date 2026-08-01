"""Deterministic loading and metric calculation for normalized ingestion JSON.

Backend owns file ingestion. This package accepts only the normalized JSON
contract, builds the single MetricStore, and never imports backend modules.
"""

from __future__ import annotations

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
    "SourceRef",
    "build_metric_store",
    "detect_axis_kind",
    "load_ingestion_file",
    "load_ingestion_result",
]
