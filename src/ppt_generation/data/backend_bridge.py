"""
呼叫 backend ingestion，取得 UnifiedIngestionResult payload
============================================================
`dataset_loader` 吃的是 backend 的 ``UnifiedIngestionResult`` JSON。在此之前
那份 JSON 得有人先產生出來——`src/backend` 只提供 FastAPI 端點與
``run_ingestion_pipeline(file_path)``，而後者一次只吃**一個檔案**。

這支模組補上中間那一段，並支援兩種輸入版型：

============ ================================================ ==================
版型         形狀                                             範例
============ ================================================ ==================
A 單檔多表   一個 .xlsx，多個工作表，每張工作表一個指標        ``source/附件四_預期修正參照資料.xlsx``
                                                              ``fixtures/data/fsc_114_workbook.xlsx``
B 多檔單表   一個目錄，多個 .xlsx，每檔一張工作表一個指標      ``fixtures/data/fsc_114/``
============ ================================================ ==================

版型 A 交給 backend 一次處理即可（它本來就會走遍所有工作表）。版型 B 要逐檔
呼叫再把 ``datasets`` 併成一份 payload——併的時候 pipeline 狀態取最嚴格的那個，
不讓某一檔的警告在合併後被稀釋掉。

兩種版型產出的 payload 形狀完全相同，所以 `dataset_loader` 與 `metric_engine`
不需要知道資料本來是幾個檔案。這是刻意的：版型是輸入端的細節，不該外溢成
下游的分支邏輯。

## 為什麼要覆寫 dataset_id

backend 的 ``dataset_id`` 是內容雜湊出來的 UUID5（見 normalizer 的
``_dataset_id()``），這對去重很好，但 `metric_engine` 是拿它組 metric_key 的：

    metric_key = f"{_slugify(dataset.dataset_id)}.value"

於是指標會叫 ``f47ac10b_58cc_....value``。LLM 只看得到指標目錄，得靠 metric_key
引用資料，一串 UUID 讓它無從判斷該用哪個指標，敘事也沒辦法寫佔位符。

所以這裡把 dataset_id 換成從工作表名／檔名推出的可讀 slug（``流通卡數``、
``有效卡數``），原本的 UUID 保留在 ``backend_dataset_id`` 欄位，追溯不會斷。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from ..core import config


#: backend 目前正式支援、且不需要 PDF／OCR 相依的輸入格式。
EXCEL_SUFFIXES = (".xlsx",)

#: pipeline 狀態的嚴格程度。合併多檔時取最嚴格（數字最大）的那個，
#: 避免 5 個檔案裡有 1 個出問題卻回報 completed。
_STATUS_SEVERITY = {
    "completed": 0,
    "completed_with_warnings": 1,
    "unsupported": 2,
    "rejected": 3,
    "failed": 4,
}


class BackendUnavailableError(RuntimeError):
    """backend ingestion 無法載入。"""


class NoExcelInputError(FileNotFoundError):
    """指定的路徑下沒有可讀的 Excel 檔。"""


def _slugify(text: str) -> str:
    """把工作表名／檔名轉成適合當 dataset_id 的片段（保留中文）。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", str(text).strip())
    return cleaned.strip("_") or "dataset"


def _load_backend():
    """
    載入 backend 的 ingestion 入口。

    `src/backend` 不是可安裝套件，它的 import 根目錄是自己那一層
    （見該層 pytest.ini 的 ``pythonpath = .``），所以要手動掛上 sys.path
    才能 ``from app.ingestion...`` 匯入。

    刻意在函式內 import，而不是在 module 層：
    這樣 ppt_generation 匯入時不會連帶要求 backend 的相依，
    只有真的要讀 Excel 的人才需要它們。
    """
    backend_root = config.PROJECT_ROOT / "src" / "backend"

    if not backend_root.exists():
        raise BackendUnavailableError(
            f"找不到 backend 目錄：{backend_root}"
        )

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    try:
        from app.ingestion.pipeline import run_ingestion_pipeline
    except ImportError as error:
        raise BackendUnavailableError(
            f"無法載入 backend ingestion：{error}。"
            f"Excel 路徑只需要 openpyxl 與 pydantic；"
            f"若缺的是 pdfplumber／pymupdf，代表有人把 PDF 剖析器"
            f"改回 module 層 import 了（那兩個相依只在 "
            f"src/backend/requirements.txt）。"
        ) from error

    return run_ingestion_pipeline


def discover_excel_files(source: str | Path) -> list[Path]:
    """
    列出要送進 backend 的 Excel 檔。

    - ``source`` 是檔案 → 就那一個（版型 A）
    - ``source`` 是目錄 → 底下所有 .xlsx，依檔名排序（版型 B）

    排序是為了讓 dataset 順序穩定：不穩定的話每次跑出來的頁面順序都不同，
    三方比對與 golden 都會無謂地漂移。以 ``~$`` 開頭的是 Excel 開檔時的
    暫存鎖檔，一律略過。
    """
    target = Path(source)

    if not target.exists():
        raise NoExcelInputError(f"找不到輸入路徑：{target}")

    if target.is_file():
        if target.suffix.lower() not in EXCEL_SUFFIXES:
            raise NoExcelInputError(
                f"{target.name} 不是 Excel 檔"
                f"（目前支援 {', '.join(EXCEL_SUFFIXES)}）"
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
        raise NoExcelInputError(
            f"{target} 底下沒有 .xlsx 檔"
        )

    return files


def _readable_dataset_id(
    dataset: dict[str, Any],
    file_stem: str,
    used: set[str],
) -> str:
    """
    推一個可讀且唯一的 dataset_id。

    優先用工作表名稱——版型 A 的工作表名就是指標名（``流通卡數``）。
    工作表名取不到時退回檔名。兩者都可能重複（不同檔案有同名工作表），
    所以先用 ``檔名_工作表名`` 消歧，還是撞的話才加序號。
    """
    sheet_name = None

    for evidence in dataset.get("evidence") or []:
        if evidence.get("sheet_name"):
            sheet_name = evidence["sheet_name"]
            break

    if sheet_name is None:
        # dataset 層沒有證據時，從第一筆資料的證據找。
        for record in dataset.get("records") or []:
            for value in (record.get("values") or {}).values():
                for evidence in value.get("evidence") or []:
                    if evidence.get("sheet_name"):
                        sheet_name = evidence["sheet_name"]
                        break

                if sheet_name:
                    break

            if sheet_name:
                break

    base = _slugify(sheet_name or dataset.get("name") or file_stem)

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


def _merge(
    results: Iterable[tuple[Path, dict[str, Any]]],
) -> dict[str, Any]:
    """
    把多份 UnifiedIngestionResult 併成一份。

    只保留下游會用到的欄位（datasets／status／warnings／errors）加上
    來源清單。backend 的 inspection／classification／extraction 等中間
    產物體積大且逐檔不同，合併後沒有明確語意，不帶進來——需要稽核時
    重跑單一檔案即可。
    """
    merged_datasets: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []
    source_files: list[str] = []
    severity = 0
    used_ids: set[str] = set()

    for path, payload in results:
        source_files.append(path.name)

        status = str(payload.get("pipeline_status", "unknown"))
        severity = max(severity, _STATUS_SEVERITY.get(status, 2))

        for message in payload.get("warnings") or []:
            warnings.append(f"[{path.name}] {message}")

        for message in payload.get("errors") or []:
            errors.append(f"[{path.name}] {message}")

        for dataset in payload.get("datasets") or []:
            dataset = dict(dataset)

            # 保留 backend 的內容雜湊 id，追溯不斷；對外改用可讀 id。
            dataset["backend_dataset_id"] = dataset.get("dataset_id")
            dataset["dataset_id"] = _readable_dataset_id(
                dataset,
                path.stem,
                used_ids,
            )

            # backend 用暫存檔跑時 filename 可能是隨機名，統一釘回真實檔名。
            dataset["filename"] = path.name

            merged_datasets.append(dataset)

    if not merged_datasets:
        detail = "；".join(warnings + errors) or "backend 未輸出任何資料集"
        raise NoExcelInputError(
            f"backend 沒有從輸入檔中抽出任何資料集：{detail}"
        )

    severity_to_status = {
        value: key for key, value in _STATUS_SEVERITY.items()
    }

    return {
        "filename": ", ".join(source_files),
        "pipeline_status": severity_to_status.get(severity, "unknown"),
        "source_files": source_files,
        "datasets": merged_datasets,
        "warnings": warnings,
        "errors": errors,
    }


def ingest_excel(
    source: str | Path,
    *,
    sheet_name: str | None = None,
) -> dict[str, Any]:
    """
    把 Excel 輸入轉成 backend 的 ``UnifiedIngestionResult`` payload（dict）。

    Args:
        source: 單一 .xlsx（版型 A）或含多個 .xlsx 的目錄（版型 B）。
        sheet_name: 只讀指定工作表。僅在 ``source`` 為單一檔案時有意義。

    Returns:
        可直接餵給 :func:`dataset_loader.load_ingestion_result` 的 dict。

    Raises:
        NoExcelInputError: 路徑不存在、不是 Excel、或抽不出任何資料集。
        BackendUnavailableError: backend ingestion 無法載入。
    """
    run_ingestion_pipeline = _load_backend()
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

        # pydantic model → 純 dict。mode="json" 讓 Enum／datetime 變成
        # 可序列化的原生型別，dataset_loader 讀到的才會是字串而非 Enum 物件。
        results.append((path, result.model_dump(mode="json")))

    return _merge(results)


def save_payload(payload: dict[str, Any], path: str | Path) -> Path:
    """把 payload 落檔，供稽核或之後用 ``--ingestion`` 重跑。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
