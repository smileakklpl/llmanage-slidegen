"""Backend-owned bridge from one or more Excel uploads to ingestion JSON.

This module is the only place that adapts uploaded workbook layouts for deck
generation. Downstream core/ppt_generation code receives the normalized JSON
contract and never imports backend implementation modules.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from app.ingestion.pipeline import run_ingestion_pipeline
from core.contracts.generation import NormalizedIngestionContract

EXCEL_SUFFIXES = (".xlsx",)
_STATUS_SEVERITY = {
    "completed": 0,
    "completed_with_warnings": 1,
    "unsupported": 2,
    "rejected": 3,
    "failed": 4,
}


class NoExcelInputError(FileNotFoundError):
    """Raised when a generation input has no readable Excel workbook."""


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text).strip())
    return cleaned.strip("_") or "dataset"


def discover_excel_files(source: str | Path) -> list[Path]:
    """Return a deterministic workbook list for a file or directory input."""
    target = Path(source)

    if not target.exists():
        raise NoExcelInputError(f"找不到輸入路徑：{target}")

    if target.is_file():
        if target.suffix.lower() not in EXCEL_SUFFIXES:
            raise NoExcelInputError(
                f"{target.name} 不是 Excel 檔（目前支援 {', '.join(EXCEL_SUFFIXES)}）"
            )
        return [target]

    files = sorted(
        path
        for path in target.iterdir()
        if path.is_file()
        and path.suffix.lower() in EXCEL_SUFFIXES
        and not path.name.startswith("~$")
    )
    if not files:
        raise NoExcelInputError(f"{target} 底下沒有 .xlsx 檔")
    return files


def _first_sheet_name(dataset: dict[str, Any]) -> str | None:
    for evidence in dataset.get("evidence") or []:
        if evidence.get("sheet_name"):
            return str(evidence["sheet_name"])

    for record in dataset.get("records") or []:
        for value in (record.get("values") or {}).values():
            for evidence in value.get("evidence") or []:
                if evidence.get("sheet_name"):
                    return str(evidence["sheet_name"])
    return None


def _readable_dataset_id(
    dataset: dict[str, Any], file_stem: str, used: set[str]
) -> str:
    base = _slugify(
        _first_sheet_name(dataset)
        or dataset.get("name")
        or file_stem
    )
    if base not in used:
        used.add(base)
        return base

    qualified = f"{_slugify(file_stem)}_{base}"
    if qualified not in used:
        used.add(qualified)
        return qualified

    index = 2
    while f"{qualified}_{index}" in used:
        index += 1
    unique = f"{qualified}_{index}"
    used.add(unique)
    return unique


def merge_ingestion_results(
    results: Iterable[tuple[Path, dict[str, Any]]],
) -> dict[str, Any]:
    """Merge per-file backend results into one normalized JSON envelope."""
    datasets: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    source_files: list[str] = []
    severity = 0
    used_ids: set[str] = set()

    for path, payload in results:
        source_files.append(path.name)
        status = str(payload.get("pipeline_status", "unknown"))
        severity = max(severity, _STATUS_SEVERITY.get(status, 2))
        warnings.extend(
            f"[{path.name}] {message}" for message in payload.get("warnings") or []
        )
        errors.extend(
            f"[{path.name}] {message}" for message in payload.get("errors") or []
        )

        for raw_dataset in payload.get("datasets") or []:
            dataset = dict(raw_dataset)
            dataset["backend_dataset_id"] = dataset.get("dataset_id")
            dataset["dataset_id"] = _readable_dataset_id(
                dataset, path.stem, used_ids
            )
            dataset["filename"] = path.name
            datasets.append(dataset)

    if not datasets:
        detail = "；".join(warnings + errors) or "backend 未輸出任何資料集"
        raise NoExcelInputError(f"backend 沒有從輸入檔中抽出任何資料集：{detail}")

    status_by_severity = {value: key for key, value in _STATUS_SEVERITY.items()}
    envelope = {
        "contract_version": "1.0",
        "filename": ", ".join(source_files),
        "pipeline_status": status_by_severity.get(severity, "unsupported"),
        "source_files": source_files,
        "datasets": datasets,
        "warnings": warnings,
        "errors": errors,
    }
    return NormalizedIngestionContract.model_validate(envelope).model_dump(
        mode="json"
    )


def ingest_excel(
    source: str | Path, *, sheet_name: str | None = None
) -> dict[str, Any]:
    """Run backend ingestion and return a JSON-compatible normalized envelope."""
    files = discover_excel_files(source)
    if sheet_name is not None and len(files) > 1:
        raise ValueError(
            "sheet_name 只能用在單一檔案輸入；"
            f"目前 {source} 底下有 {len(files)} 個檔案"
        )

    results: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        result = run_ingestion_pipeline(
            path,
            original_filename=path.name,
            sheet_name=sheet_name,
        )
        results.append((path, result.model_dump(mode="json")))
    return merge_ingestion_results(results)


def save_payload(payload: dict[str, Any], path: str | Path) -> Path:
    """Persist an ingestion JSON envelope for the core generation contract."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
