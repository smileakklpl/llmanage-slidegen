"""
MetricStore：系統唯一真相來源
==============================
對應 docs/圖表原生性與資料同步設計.md Stage 3。

兩個對外介面刻意分離，這是「LLM 不產生數字」原則的技術落點：

- :meth:`MetricStore.catalog_for_llm` ── 只給 metadata，**不含任何實際數值**
- :meth:`MetricStore.get` ── 查表取實際數值，只允許本地確定性程式呼叫

LLM 看得到「有哪些指標可用、單位是什麼、類別長什麼樣」，
但看不到數字本身，因此無法把數字寫進輸出，只能回傳 metric_key 引用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

#: catalog 提供給 LLM 的類別預覽最多幾項，避免 prompt 過長。
CATEGORY_PREVIEW_LIMIT = 6


class MetricNotFoundError(KeyError):
    """查詢了不存在的 metric_key。"""


class MetricNotComputableError(ValueError):
    """
    指標存在但被標記為不可計算（防呆），不得用於簡報。

    典型情境：只有單一年度資料時要求 YoY —— 引擎會建立佔位的
    metric 但標記 computable=False，並記錄理由，避免下游誤用。
    """


@dataclass(frozen=True)
class SourceRef:
    """單一數值的來源追溯資訊，直接承接 backend 的 evidence。"""

    filename: str
    sheet_name: str | None = None
    cell: str | None = None
    cell_range: str | None = None
    page_number: int | None = None
    extraction_method: str = "unknown"
    confidence: float = 1.0

    def describe(self) -> str:
        """人類可讀的來源描述，用於稽核 Excel 的來源欄位。"""
        parts = [self.filename]

        if self.sheet_name:
            parts.append(self.sheet_name)

        location = self.cell or self.cell_range

        if location:
            parts.append(location)

        if self.page_number is not None:
            parts.append(f"p.{self.page_number}")

        return " / ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "sheet_name": self.sheet_name,
            "cell": self.cell,
            "cell_range": self.cell_range,
            "page_number": self.page_number,
            "extraction_method": self.extraction_method,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SourceRef:
        return cls(
            filename=payload.get("filename", "unknown"),
            sheet_name=payload.get("sheet_name"),
            cell=payload.get("cell"),
            cell_range=payload.get("cell_range"),
            page_number=payload.get("page_number"),
            extraction_method=payload.get("extraction_method", "unknown"),
            confidence=float(payload.get("confidence", 1.0)),
        )


@dataclass
class MetricSeries:
    """
    一個指標的完整資料。

    對應到一張圖表所需的全部數值：``categories`` 是 x 軸（月份、銀行名稱
    等），``series`` 是每組系列對應的數值，長度必須與 categories 一致。
    """

    metric_key: str
    name: str
    categories: list[str]
    series: dict[str, list[float | None]]
    unit: str | None = None
    #: 指標語意類型，供 reviewer 判斷圖表類型是否合適（如 share 不該用折線圖）
    semantic: str = "value"
    #: 類別軸語意：``temporal``（時間序列）或 ``categorical``（橫斷面分類）。
    #: 決定哪些衍生指標有意義（跨時間才談成長率，跨機構才談占比／排名）。
    axis_kind: str = "categorical"
    #: 防呆旗標：False 代表資料範圍不足以計算此指標，禁止用於簡報
    computable: bool = True
    #: computable=False 的原因，或其他需提醒使用者的事項
    notes: list[str] = field(default_factory=list)
    #: 來源追溯，key 為 f"{series_name}|{category}"，缺項代表該值為衍生計算
    evidence: dict[str, SourceRef] = field(default_factory=dict)
    #: 衍生指標的計算公式描述（可追溯性要求）
    formula: str | None = None
    #: 是否需人工確認（承接 backend 的 requires_human_review）
    requires_human_review: bool = False

    def __post_init__(self) -> None:
        for series_name, values in self.series.items():
            if len(values) != len(self.categories):
                raise ValueError(
                    f"指標 {self.metric_key!r} 的系列 {series_name!r} 長度 "
                    f"{len(values)} 與類別數 {len(self.categories)} 不符"
                )

    @property
    def series_names(self) -> list[str]:
        return list(self.series)

    def values_for(self, series_name: str) -> list[float | None]:
        if series_name not in self.series:
            raise MetricNotFoundError(
                f"指標 {self.metric_key!r} 中沒有系列 {series_name!r}，"
                f"可用系列：{self.series_names}"
            )

        return list(self.series[series_name])

    def source_of(self, series_name: str, category: str) -> SourceRef | None:
        """取得某格數值的來源。衍生指標可能沒有直接來源，回傳 None。"""
        return self.evidence.get(f"{series_name}|{category}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_key": self.metric_key,
            "name": self.name,
            "categories": list(self.categories),
            "series": {name: list(values) for name, values in self.series.items()},
            "unit": self.unit,
            "semantic": self.semantic,
            "axis_kind": self.axis_kind,
            "computable": self.computable,
            "notes": list(self.notes),
            "evidence": {key: ref.to_dict() for key, ref in self.evidence.items()},
            "formula": self.formula,
            "requires_human_review": self.requires_human_review,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MetricSeries:
        return cls(
            metric_key=payload["metric_key"],
            name=payload["name"],
            categories=list(payload["categories"]),
            series={
                name: list(values)
                for name, values in payload.get("series", {}).items()
            },
            unit=payload.get("unit"),
            semantic=payload.get("semantic", "value"),
            axis_kind=payload.get("axis_kind", "categorical"),
            computable=payload.get("computable", True),
            notes=list(payload.get("notes", [])),
            evidence={
                key: SourceRef.from_dict(ref)
                for key, ref in payload.get("evidence", {}).items()
            },
            formula=payload.get("formula"),
            requires_human_review=payload.get("requires_human_review", False),
        )


@dataclass
class MetricStore:
    """
    指標集合。系統中所有簡報數字的唯一來源。

    典型用法::

        store = metric_engine.build_metric_store(ingestion_result)
        catalog = store.catalog_for_llm()      # 交給 LLM 決策用（無數值）
        series = store.get("market.card_count")  # 本地查表取值
    """

    metrics: dict[str, MetricSeries] = field(default_factory=dict)
    #: 資料來源檔名清單，供稽核輸出標注
    source_files: list[str] = field(default_factory=list)

    def add(self, series: MetricSeries) -> None:
        if series.metric_key in self.metrics:
            raise ValueError(f"metric_key 重複：{series.metric_key!r}")

        self.metrics[series.metric_key] = series

    def __contains__(self, metric_key: object) -> bool:
        return metric_key in self.metrics

    def __len__(self) -> int:
        return len(self.metrics)

    def get(self, metric_key: str, *, require_computable: bool = True) -> MetricSeries:
        """
        查表取得指標實際數值。

        Raises:
            MetricNotFoundError: metric_key 不存在。
            MetricNotComputableError: 指標被防呆標記為不可計算。
        """
        series = self.metrics.get(metric_key)

        if series is None:
            raise MetricNotFoundError(
                f"MetricStore 中找不到 {metric_key!r}。"
                f"可用指標：{sorted(self.metrics)}"
            )

        if require_computable and not series.computable:
            reason = "；".join(series.notes) or "資料範圍不足"
            raise MetricNotComputableError(
                f"指標 {metric_key!r} 不可用於簡報：{reason}"
            )

        return series

    def computable_metric_keys(self) -> list[str]:
        """可用於簡報的指標白名單。Reviewer 與 ChartPlanner 的防呆依據。"""
        return sorted(
            key for key, series in self.metrics.items() if series.computable
        )

    def blocked_metrics(self) -> dict[str, list[str]]:
        """被防呆擋下的指標及其原因，用於回報使用者「為什麼沒有這頁」。"""
        return {
            key: list(series.notes)
            for key, series in self.metrics.items()
            if not series.computable
        }

    # -----------------------------------------------------------------
    # 給 LLM 的視圖
    # -----------------------------------------------------------------
    def catalog_for_llm(
        self,
        *,
        category_preview_limit: int = CATEGORY_PREVIEW_LIMIT,
    ) -> list[dict[str, Any]]:
        """
        產生給 LLM 的指標目錄。

        **刻意不包含任何實際數值**，只提供 metadata：metric_key、名稱、
        單位、語意類型、系列名稱、類別數量與前幾項類別預覽。

        類別名稱（如月份、銀行名稱）本身不是「計算結果」，屬於 LLM 判斷
        圖表類型所必需的資訊，因此允許出現在 catalog；數值則完全排除。
        """
        catalog: list[dict[str, Any]] = []

        for metric_key in self.computable_metric_keys():
            series = self.metrics[metric_key]
            preview = list(series.categories[:category_preview_limit])

            catalog.append(
                {
                    "metric_key": metric_key,
                    "name": series.name,
                    "unit": series.unit,
                    "semantic": series.semantic,
                    "axis_kind": series.axis_kind,
                    "series_names": series.series_names,
                    "category_count": len(series.categories),
                    "category_preview": preview,
                    "category_truncated": len(series.categories) > len(preview),
                }
            )

        return catalog

    def catalog_as_prompt(self, **kwargs: Any) -> str:
        """把 catalog 轉成放進 prompt 的 JSON 字串。"""
        return json.dumps(
            self.catalog_for_llm(**kwargs),
            ensure_ascii=False,
            indent=2,
        )

    # -----------------------------------------------------------------
    # 持久化（供 DeckSpec Refresh 重放）
    # -----------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "source_files": list(self.source_files),
            "metrics": [series.to_dict() for series in self.metrics.values()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> MetricStore:
        store = cls(source_files=list(payload.get("source_files", [])))

        for item in payload.get("metrics", []):
            store.add(MetricSeries.from_dict(item))

        return store

    def save_json(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return target

    @classmethod
    def load_json(cls, path: str | Path) -> MetricStore:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(payload)


def merge_stores(stores: Iterable[MetricStore]) -> MetricStore:
    """
    合併多個 MetricStore（多份上傳檔案的情境）。

    metric_key 衝突時直接拋錯，不做靜默覆寫 —— 數字來源必須明確。
    """
    merged = MetricStore()

    for store in stores:
        for key, series in store.metrics.items():
            if key in merged:
                raise ValueError(
                    f"合併 MetricStore 時 metric_key 衝突：{key!r}"
                )

            merged.metrics[key] = series

        for filename in store.source_files:
            if filename not in merged.source_files:
                merged.source_files.append(filename)

    return merged


def align_series(
    categories: Sequence[str],
    mapping: dict[str, float | None],
) -> list[float | None]:
    """
    依 categories 順序展開一個 {類別: 值} 對應表，缺項補 None。

    用於指標引擎組裝資料時保證系列長度與類別對齊。
    """
    return [mapping.get(category) for category in categories]
