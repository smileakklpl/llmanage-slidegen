"""
指標計算引擎（確定性，零 LLM 參與）
=====================================
對應 docs/圖表原生性與資料同步設計.md Stage 2。

輸入：:class:`dataset_loader.LoadResult`
輸出：:class:`metric_store.MetricStore`

這是系統中**唯一**允許產生數字的地方。所有 YoY／MoM／市占率／排名／
趨勢外推都在此以 pandas 計算，並保留公式描述與來源追溯。

防呆原則（產品原則 4）：資料範圍不足時，指標仍會被建立但標記
``computable=False`` 並記錄理由，讓下游能明確回報「為什麼沒有這頁」，
而不是靜默消失或產生錯誤數字。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from .data_profile import DatasetProfile, MeasureProfile, profile_dataset
from .dataset_loader import ColumnMeta, LoadedDataset, LoadResult
from .metric_store import MetricSeries, MetricStore, SourceRef


#: 計算成長率所需的最少期數。
MIN_PERIODS_FOR_GROWTH = 2

#: 線性外推所需的最少歷史點數。
MIN_POINTS_FOR_FORECAST = 4

#: 數值四捨五入位數。避免浮點誤差讓三方比對出現尾差。
ROUND_DIGITS = 4

#: Top N 切片的預設 N。附件三多頁都是「Top 10 銀行」。
DEFAULT_TOP_N = 10


@dataclass
class EngineConfig:
    """指標引擎行為設定。"""

    #: 是否計算期間成長率（MoM／QoQ，視資料頻率而定）
    enable_period_growth: bool = True
    #: 是否計算系列間年增率（欄位代表不同年度時）
    enable_yoy: bool = True
    #: 是否計算占比（市占率）
    enable_share: bool = True
    #: 是否計算排名
    enable_rank: bool = True
    #: 是否做趨勢外推。預設開啟：FR-2.6 的「未來趨勢推測」章節一律引用
    #: forecast 類指標，關掉的話那個章節就沒有東西可放。用在非時間軸的
    #: 指標上仍會被防呆擋下，所以開著是安全的。
    enable_forecast: bool = True
    #: 外推期數
    forecast_periods: int = 3
    #: 是否額外提供 Top N 切片（33 家銀行無法畫圓餅或做表，見 derive_top）
    enable_top_n: bool = True
    #: 是否把交叉表轉成市場層級期間序列（見 build_market_timeline）
    enable_market_timeline: bool = True
    #: Top N 的 N
    top_n: int = DEFAULT_TOP_N


@dataclass
class EngineReport:
    """引擎執行摘要，供回報使用者與稽核。"""

    metric_count: int = 0
    blocked: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    dataset_profiles: list[dict[str, object]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")
_ROC_YEAR_PATTERN = re.compile(r"^\s*(1\d{2})\s*年?\s*$")

#: 時間軸類別的常見寫法：民國/西元年、季、月、日期、Q1、FY 等。
_TEMPORAL_PATTERNS = (
    re.compile(r"^\s*\d{1,2}\s*月"),
    re.compile(r"^\s*(19|20)\d{2}\s*[年/-]?"),
    re.compile(r"^\s*(第\s*)?[1-4一二三四]\s*季"),
    re.compile(r"^\s*[Qq][1-4]\b"),
    re.compile(r"^\s*[Ff][Yy]\s*\d{2,4}"),
    re.compile(r"^\s*\d{4}[-/]\d{1,2}([-/]\d{1,2})?\s*$"),
    re.compile(r"^\s*\d{1,2}[-/]\d{1,2}\s*$"),
    re.compile(r"^\s*上|下\s*半年"),
    # 民國年月（11401）與民國年（114）：金管會月報的欄名就是這個形式，
    # 交叉表轉置後會成為類別軸。不認它的話，轉置出來的時間序列會被判成
    # 橫斷面分類，趨勢與外推全部被防呆擋掉。
    re.compile(r"^\s*1\d{2}(0[1-9]|1[0-2])\s*$"),
    re.compile(r"^\s*1\d{2}\s*$"),
    # 西元年月（202601）
    re.compile(r"^\s*(19|20)\d{2}(0[1-9]|1[0-2])\s*$"),
)

#: 類別軸語意類型。
AXIS_TEMPORAL = "temporal"
AXIS_CATEGORICAL = "categorical"

#: 合計列的常見寫法。金管會月報用「總計」，其他報表可能寫「合計」或「小計」。
#: 比對前會去空白並轉小寫，所以這裡只列正規化後的形式。
_TOTAL_LABELS = frozenset(
    {
        "總計",
        "合計",
        "小計",
        "總和",
        "全體",
        "全市場",
        "市場總計",
        "total",
        "subtotal",
        "sum",
        "grandtotal",
    }
)


def is_total_category(label: str) -> bool:
    """
    判斷某個類別是否為合計列。

    合計列不是一個與其他類別並列的實體，而是它們的加總。若把它當成
    同層類別，占比與排名都會錯：

    - 占比：分母變成兩倍（各機構總和 + 合計），每一家的市占率都少一半
    - 排名：合計列一定最大，會佔據第 1 名並使其後所有名次位移 1

    這條規則與 ``config/metric_definitions.json`` 的 ``ranking`` 定義一致
    （「必須先排除 total_row」），該處記錄了用錯定義的實測影響。
    """
    normalized = re.sub(r"[\s　]+", "", str(label)).lower()
    return normalized in _TOTAL_LABELS


def _slugify(text: str) -> str:
    """把欄位／資料集名稱轉成適合放進 metric_key 的片段。"""
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", text.strip())
    return cleaned.strip("_").lower() or "unnamed"


def _round(value: float | None) -> float | None:
    if value is None:
        return None

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None

    return round(float(value), ROUND_DIGITS)


def _clean_values(raw: Sequence[object]) -> list[float | None]:
    """把 pandas 取出的值轉為 float 或 None（NaN → None）。"""
    cleaned: list[float | None] = []

    for item in raw:
        if item is None or (isinstance(item, float) and math.isnan(item)):
            cleaned.append(None)
        else:
            cleaned.append(_round(float(item)))

    return cleaned


def _detect_year(label: str) -> int | None:
    match = _YEAR_PATTERN.search(label)
    if match:
        return int(match.group())

    # 民國年只接受完整三位年度標籤（113、114年）；11401 是年月，
    # 不可誤判成年度系列，否則會把相鄰月份拿來計算 YoY。
    roc_match = _ROC_YEAR_PATTERN.fullmatch(label)
    return int(roc_match.group(1)) if roc_match else None


def _category_column(dataset: LoadedDataset) -> ColumnMeta | None:
    """
    挑選作為類別軸的欄位。

    優先取第一個非數值欄位（月份、銀行名稱、科目名稱），
    這對齊附件四類報表「首欄為項目名稱」的慣例。
    """
    label_columns = sorted(dataset.label_columns(), key=lambda meta: meta.index)
    return label_columns[0] if label_columns else None


def _categories_of(dataset: LoadedDataset, column: ColumnMeta | None) -> list[str]:
    if column is None:
        # 沒有標籤欄位時，退化用列序號當類別，仍可畫圖但語意較弱。
        return [f"第{index + 1}列" for index in range(len(dataset.frame))]

    return [
        "" if value is None else str(value)
        for value in dataset.frame[column.key].tolist()
    ]


def detect_axis_kind(
    categories: Sequence[str],
    column: ColumnMeta | None = None,
) -> str:
    """
    判斷類別軸是「時間序列」還是「橫斷面分類」。

    這是防呆的關鍵判斷：跨時間才能談成長率與趨勢外推，跨機構／科目
    才能談占比與排名。若把銀行名稱當時間軸做外推，會產出「預測+1 期
    的銀行卡數」這種無意義甚至負值的結果。

    判斷依據（任一成立即視為時間軸）：
    1. backend 已標記欄位為 date／datetime 型別
    2. 過半類別標籤符合年／季／月／日期等時間寫法
    """
    if column is not None and column.is_temporal:
        return AXIS_TEMPORAL

    if not categories:
        return AXIS_CATEGORICAL

    matched = sum(
        1
        for label in categories
        if any(pattern.match(str(label)) for pattern in _TEMPORAL_PATTERNS)
    )

    if matched * 2 > len(categories):
        return AXIS_TEMPORAL

    return AXIS_CATEGORICAL


def _blocked_metric(
    base: MetricSeries,
    suffix: str,
    name_suffix: str,
    unit: str | None,
    semantic: str,
    reasons: list[str],
) -> MetricSeries:
    """建立一個被防呆標記為不可計算的佔位指標，保留原因供回報使用者。"""
    return MetricSeries(
        metric_key=base.metric_key.replace(".value", suffix),
        name=f"{base.name} {name_suffix}",
        categories=list(base.categories),
        series={},
        unit=unit,
        semantic=semantic,
        value_semantic=base.value_semantic,
        aggregation_semantic=base.aggregation_semantic,
        allowed_derivations=list(base.allowed_derivations),
        shape_kind=base.shape_kind,
        axis_kind=base.axis_kind,
        computable=False,
        notes=reasons,
    )


def _evidence_map(
    dataset: LoadedDataset,
    numeric_columns: Sequence[ColumnMeta],
    categories: Sequence[str],
) -> dict[str, SourceRef]:
    """建立 f"{系列名}|{類別}" → SourceRef 的對應。"""
    evidence: dict[str, SourceRef] = {}
    record_indices = dataset.frame["__record_index__"].tolist()

    for meta in numeric_columns:
        for position, record_index in enumerate(record_indices):
            source = dataset.source_of(meta.key, int(record_index))

            if source is not None and position < len(categories):
                evidence[f"{meta.label}|{categories[position]}"] = source

    return evidence


# ---------------------------------------------------------------------------
# 基礎指標
# ---------------------------------------------------------------------------
def _profiled_metric(
    dataset: LoadedDataset,
    profile: DatasetProfile,
    measure: MeasureProfile,
    numeric_columns: Sequence[ColumnMeta],
    *,
    metric_key: str,
    name: str,
) -> MetricSeries:
    category_column = _category_column(dataset)
    categories = _categories_of(dataset, category_column)
    series = {
        meta.label: _clean_values(dataset.frame[meta.key].tolist())
        for meta in numeric_columns
    }
    units = {meta.unit for meta in numeric_columns if meta.unit}
    notes = list(dataset.warnings)
    notes.append(f"語意判定：{measure.inference_reason}")
    if profile.shape_kind == "multi_dimension":
        notes.append(
            "資料含多個維度；目前以第一個類別欄為視圖，未進行跨維度加總"
        )

    return MetricSeries(
        metric_key=metric_key,
        name=name,
        categories=categories,
        series=series,
        unit=units.pop() if len(units) == 1 else measure.unit,
        series_units={meta.label: meta.unit for meta in numeric_columns},
        semantic="value",
        value_semantic=measure.value_semantic,
        aggregation_semantic=measure.aggregation_semantic,
        allowed_derivations=list(measure.allowed_derivations),
        shape_kind=profile.shape_kind,
        axis_kind=detect_axis_kind(categories, category_column),
        evidence=_evidence_map(dataset, numeric_columns, categories),
        requires_human_review=dataset.requires_human_review,
        notes=notes,
    )


def build_base_metrics(
    dataset: LoadedDataset,
    profile: DatasetProfile | None = None,
) -> list[MetricSeries]:
    """Build safe raw metrics, splitting long tables by measure semantics."""
    numeric_columns = sorted(dataset.numeric_columns(), key=lambda item: item.index)
    if not numeric_columns:
        return []

    resolved_profile = profile or profile_dataset(dataset)
    dataset_slug = _slugify(dataset.dataset_id)

    if resolved_profile.shape_kind == "entity_by_period":
        measure = resolved_profile.measures[0]
        return [
            _profiled_metric(
                dataset,
                resolved_profile,
                measure,
                numeric_columns,
                metric_key=f"{dataset_slug}.value",
                name=dataset.name,
            )
        ]

    metrics: list[MetricSeries] = []
    multiple = len(numeric_columns) > 1
    profiles = {item.key: item for item in resolved_profile.measures}
    for column in numeric_columns:
        measure = profiles[column.key]
        key = (
            f"{dataset_slug}.{_slugify(column.label)}.value"
            if multiple
            else f"{dataset_slug}.value"
        )
        name = f"{dataset.name}－{column.label}" if multiple else dataset.name
        metrics.append(
            _profiled_metric(
                dataset,
                resolved_profile,
                measure,
                [column],
                metric_key=key,
                name=name,
            )
        )
    return metrics


def build_base_metric(dataset: LoadedDataset) -> MetricSeries | None:
    """Compatibility wrapper returning the first profiled raw metric."""
    metrics = build_base_metrics(dataset)
    return metrics[0] if metrics else None


# ---------------------------------------------------------------------------
# 衍生指標
# ---------------------------------------------------------------------------
def derive_period_growth(base: MetricSeries) -> MetricSeries:
    """
    期間成長率（相鄰類別之間，對應 MoM／QoQ）。

    第一期沒有前期可比，值為 None —— 不編造基期，也不補 0。
    """
    metric_key = base.metric_key.replace(".value", ".period_growth")

    if "period_growth" not in base.allowed_derivations:
        return _blocked_metric(
            base,
            ".period_growth",
            "期間成長率",
            "%",
            "growth",
            [
                f"measure semantic={base.value_semantic} 未核准期間成長率推導"
            ],
        )

    # 橫斷面資料（銀行、科目）相鄰兩項相減沒有「成長」語意，直接擋下。
    if base.axis_kind != AXIS_TEMPORAL:
        return _blocked_metric(
            base,
            ".period_growth",
            "期間成長率",
            "%",
            "growth",
            [
                "類別軸為橫斷面分類（非時間序列），"
                "相鄰類別相比不具成長率語意，不予計算"
            ],
        )

    if len(base.categories) < MIN_PERIODS_FOR_GROWTH:
        return _blocked_metric(
            base,
            ".period_growth",
            "期間成長率",
            "%",
            "growth",
            [
                f"僅有 {len(base.categories)} 期資料，"
                f"少於計算成長率所需的 {MIN_PERIODS_FOR_GROWTH} 期"
            ],
        )

    growth: dict[str, list[float | None]] = {}

    for series_name, values in base.series.items():
        row: list[float | None] = [None]

        for index in range(1, len(values)):
            previous = values[index - 1]
            current = values[index]

            if previous in (None, 0) or current is None:
                row.append(None)
            else:
                row.append(_round((current - previous) / previous * 100))

        growth[series_name] = row

    return MetricSeries(
        metric_key=metric_key,
        name=f"{base.name} 期間成長率",
        categories=list(base.categories),
        series=growth,
        unit="%",
        semantic="growth",
        value_semantic=base.value_semantic,
        aggregation_semantic=base.aggregation_semantic,
        allowed_derivations=list(base.allowed_derivations),
        shape_kind=base.shape_kind,
        axis_kind=base.axis_kind,
        formula="(本期 - 前期) / 前期 × 100%",
        requires_human_review=base.requires_human_review,
    )


def derive_yoy(base: MetricSeries) -> MetricSeries:
    """
    年增率（YoY）：系列名稱各自代表年度時，相鄰年度相比。

    防呆重點：只有單一年度資料時，明確標記不可計算，
    對應產品原則 4「僅有單年資料時不得計算/呈現 YoY」。
    """
    metric_key = base.metric_key.replace(".value", ".yoy")

    if "yoy" not in base.allowed_derivations:
        return _blocked_metric(
            base,
            ".yoy",
            "年增率",
            "%",
            "growth",
            [f"measure semantic={base.value_semantic} 未核准 YoY 推導"],
        )

    years: list[tuple[int, str]] = []

    for series_name in base.series:
        year = _detect_year(series_name)

        if year is not None:
            years.append((year, series_name))

    years.sort()

    if len(years) < 2:
        detected = [name for _, name in years]
        return _blocked_metric(
            base,
            ".yoy",
            "年增率",
            "%",
            "growth",
            [
                "資料中無法辨識出兩個以上的年度系列，不計算 YoY。"
                f"辨識到的年度系列：{detected or '無'}"
            ],
        )

    yoy: dict[str, list[float | None]] = {}

    for position in range(1, len(years)):
        previous_year, previous_name = years[position - 1]
        current_year, current_name = years[position]

        previous_values = base.series[previous_name]
        current_values = base.series[current_name]

        row: list[float | None] = []

        for index in range(len(base.categories)):
            previous = previous_values[index]
            current = current_values[index]

            if previous in (None, 0) or current is None:
                row.append(None)
            else:
                row.append(_round((current - previous) / previous * 100))

        yoy[f"{current_year} vs {previous_year}"] = row

    return MetricSeries(
        metric_key=metric_key,
        name=f"{base.name} 年增率",
        categories=list(base.categories),
        series=yoy,
        unit="%",
        semantic="growth",
        value_semantic=base.value_semantic,
        aggregation_semantic=base.aggregation_semantic,
        allowed_derivations=list(base.allowed_derivations),
        shape_kind=base.shape_kind,
        axis_kind=base.axis_kind,
        formula="(當年 - 去年) / 去年 × 100%",
        requires_human_review=base.requires_human_review,
    )


def derive_share(base: MetricSeries) -> MetricSeries:
    """
    占比／市占率：每個類別占該系列總和的百分比。

    僅在數值全為非負時才計算 —— 含負值的資料算占比沒有商業意義
    （例如損益表的虧損項），此時標記不可計算。

    分母排除合計列（見 :func:`is_total_category`）。來源報表若自帶「總計」
    列而未排除，每一家的市占率都會剛好少一半。合計列本身的占比記為 None，
    不是 100% —— 它不是一個參與競爭的實體，給它一個占比會讓圖表出現一根
    與其他人不同性質的長條。
    """
    metric_key = base.metric_key.replace(".value", ".share")

    if (
        "share" not in base.allowed_derivations
        or base.aggregation_semantic != "sum"
    ):
        return _blocked_metric(
            base,
            ".share",
            "占比",
            "%",
            "share",
            [
                f"measure semantic={base.value_semantic}、"
                f"aggregation={base.aggregation_semantic} 非可加總占比"
            ],
        )

    # 時間軸上算「各月占全年的比例」不是市占率，商業意義薄弱且易誤導，
    # 因此占比只在橫斷面分類（銀行、通路、卡種）上計算。
    if base.axis_kind == AXIS_TEMPORAL:
        return _blocked_metric(
            base,
            ".share",
            "占比",
            "%",
            "share",
            [
                "類別軸為時間序列，各期占總和之比例非市占率語意，不予計算"
            ],
        )

    shares: dict[str, list[float | None]] = {}
    blocked_reasons: list[str] = []
    notes: list[str] = []

    is_total = [is_total_category(label) for label in base.categories]
    total_labels = [
        label
        for label, flagged in zip(base.categories, is_total)
        if flagged
    ]

    if total_labels:
        notes.append(
            f"分母已排除合計列：{'、'.join(total_labels)}"
        )

    for series_name, values in base.series.items():
        # 合計列不參與分母，也不給自己一個占比。
        present = [
            value
            for value, flagged in zip(values, is_total)
            if value is not None and not flagged
        ]

        if not present:
            blocked_reasons.append(f"系列 {series_name} 沒有可用數值")
            continue

        if any(value < 0 for value in present):
            blocked_reasons.append(
                f"系列 {series_name} 含負值，占比無商業意義"
            )
            continue

        total = sum(present)

        if total == 0:
            blocked_reasons.append(f"系列 {series_name} 總和為 0，無法計算占比")
            continue

        shares[series_name] = [
            None
            if value is None or flagged
            else _round(value / total * 100)
            for value, flagged in zip(values, is_total)
        ]

    if not shares:
        return _blocked_metric(
            base,
            ".share",
            "占比",
            "%",
            "share",
            blocked_reasons or ["無可計算占比的系列"],
        )

    return MetricSeries(
        metric_key=metric_key,
        name=f"{base.name} 占比",
        categories=list(base.categories),
        series=shares,
        unit="%",
        semantic="share",
        axis_kind=base.axis_kind,
        formula="各類別值 / 該系列總和（不含合計列）× 100%",
        notes=notes + blocked_reasons,
        requires_human_review=base.requires_human_review,
    )


def derive_rank(base: MetricSeries) -> MetricSeries:
    """
    排名：每個系列內由大到小排名（1 為最大）。

    類別數少於 2 時無排名意義，標記不可計算。

    合計列不參與排名（見 :func:`is_total_category`）。它一定是最大值，
    不排除的話會佔據第 1 名，其後每一家的名次都往後位移一位。
    """
    metric_key = base.metric_key.replace(".value", ".rank")

    if "rank" not in base.allowed_derivations:
        return _blocked_metric(
            base,
            ".rank",
            "排名",
            "名",
            "rank",
            [f"measure semantic={base.value_semantic} 未核准排名推導"],
        )

    # 對月份排名（「3月是第 1 名」）不具商業意義，排名限橫斷面分類。
    if base.axis_kind == AXIS_TEMPORAL:
        return _blocked_metric(
            base,
            ".rank",
            "排名",
            "名",
            "rank",
            ["類別軸為時間序列，對期間排名不具商業意義，不予計算"],
        )

    if len(base.categories) < 2:
        return _blocked_metric(
            base,
            ".rank",
            "排名",
            "名",
            "rank",
            ["類別數少於 2，排名無意義"],
        )

    ranks: dict[str, list[float | None]] = {}
    notes: list[str] = []

    is_total = [is_total_category(label) for label in base.categories]
    total_labels = [
        label
        for label, flagged in zip(base.categories, is_total)
        if flagged
    ]

    if total_labels:
        notes.append(f"排名已排除合計列：{'、'.join(total_labels)}")

    for series_name, values in base.series.items():
        # 合計列先換成 NaN，排名時自然被跳過，其餘名次不會位移。
        column = pd.Series(
            [
                None if flagged else value
                for value, flagged in zip(values, is_total)
            ],
            dtype="float64",
        )
        # method="min" 讓並列名次共用較小的排名（標準商業排名慣例）
        ranked = column.rank(ascending=False, method="min")

        ranks[series_name] = [
            None if math.isnan(value) else int(value) for value in ranked.tolist()
        ]

    return MetricSeries(
        metric_key=metric_key,
        name=f"{base.name} 排名",
        categories=list(base.categories),
        series=ranks,
        unit="名",
        semantic="rank",
        axis_kind=base.axis_kind,
        formula="同系列內由大到小排序（不含合計列），並列取較小名次",
        notes=notes,
        requires_human_review=base.requires_human_review,
    )


def is_period_label(label: str) -> bool:
    """這個標籤看起來像一個期間嗎（月份／年／民國年月／日期）。"""
    return any(pattern.match(str(label)) for pattern in _TEMPORAL_PATTERNS)


def _market_total_for_column(
    labels: Sequence[str],
    values: Sequence[float | None],
) -> tuple[float | None, str]:
    """
    取某一期的市場總量，並回報是怎麼取的。

    優先用來源報表自帶的「總計」列——那是主管機關公告的數字，比我們自己
    加總更權威（也涵蓋未逐家列出的機構）。沒有總計列時才加總各機構。
    """
    for label, value in zip(labels, values):
        if is_total_category(label) and value is not None:
            return value, "來源報表「總計」列"

    present = [value for value in values if value is not None]

    if not present:
        return None, "無資料"

    return _round(sum(present)), "各機構加總"


def build_market_timeline(
    metrics: Sequence[MetricSeries],
    *,
    # 鍵尾一律是 .value：衍生指標是用 ``metric_key.replace(".value", suffix)``
    # 換出來的，少了這個尾巴，period_growth 與 forecast 會拿到同一個鍵而相撞。
    metric_key: str = "aggregate_by_period.value",
    name: str = "整體彙總（各期）",
) -> MetricSeries | None:
    """
    把多份 entity × period 交叉表轉成一條市場層級的期間序列。

    為什麼需要這個
    --------------
    金管會月報的每個指標都是「機構 × 期間」的交叉表，類別軸是機構。這種
    形狀能算市占率與排名，但**算不出趨勢**——`derive_period_growth`、
    `derive_yoy`、`derive_forecast` 全部會被防呆正確地擋掉，因為對機構名稱
    做外推毫無意義。

    但附件三的主打頁 P.5 要的正是「12 個月的流通卡數（長條）+ 簽帳金額
    （折線）」：類別軸是期間，而且兩個系列來自**不同**的指標。一張圖只能
    引用一個 metric_key，所以這裡把各指標的每期市場總量合併成單一指標，
    系列名即指標名。有了它，雙軸圖、趨勢線、以及 FR-2.6 的「未來趨勢推測」
    章節（需要 forecast 指標）才有東西可用。

    ``unit`` 刻意留 None：卡數（張）與金額（百萬元）本來就不同單位，
    謊稱一個共同單位比留空更糟——量級差異正是這張圖要用雙軸的理由。

    Args:
        metrics: 候選指標。只會採用「橫斷面類別軸 + 期間型系列名」的原始值
            指標，其餘一律略過。

    Returns:
        轉置後的 :class:`MetricSeries`（``axis_kind`` 為 temporal），
        沒有任何可用來源時回傳 None。
    """
    usable = [
        metric
        for metric in metrics
        if metric.computable
        and metric.semantic == "value"
        and metric.axis_kind == AXIS_CATEGORICAL
        and metric.aggregation_semantic == "sum"
        and metric.shape_kind in {"entity_by_period", "unknown"}
        and len(metric.series) >= MIN_PERIODS_FOR_GROWTH
        and all(is_period_label(series) for series in metric.series_names)
    ]

    if not usable:
        return None

    periods = usable[0].series_names
    series: dict[str, list[float | None]] = {}
    series_units: dict[str, str | None] = {}
    notes: list[str] = []
    evidence: dict[str, SourceRef] = {}

    for metric in usable:
        if metric.series_names != periods:
            notes.append(
                f"指標 {metric.name} 的期間與其他指標不一致，未納入"
                f"（{metric.series_names[:3]}…）"
            )
            continue

        totals: list[float | None] = []
        sources: set[str] = set()

        for period in periods:
            total, how = _market_total_for_column(
                metric.categories, metric.series[period]
            )
            totals.append(total)
            sources.add(how)

            source = next(
                (
                    metric.source_of(period, label)
                    for label in metric.categories
                    if is_total_category(label)
                ),
                None,
            )

            if source is not None:
                evidence[f"{metric.name}|{period}"] = source

        source_units = {metric.unit_for(period) for period in periods}
        series_unit = source_units.pop() if len(source_units) == 1 else None
        series[metric.name] = totals
        series_units[metric.name] = series_unit
        notes.append(
            f"{metric.name}（{series_unit or '未標示單位'}）"
            f"每期總量取自：{'、'.join(sorted(sources))}"
        )

    if not series:
        return None

    return MetricSeries(
        metric_key=metric_key,
        name=name,
        categories=list(periods),
        series=series,
        unit=None,
        series_units=series_units,
        semantic="value",
        value_semantic="aggregate",
        aggregation_semantic="sum",
        allowed_derivations=["period_growth", "yoy", "forecast"],
        shape_kind="aggregate_timeline",
        axis_kind=AXIS_TEMPORAL,
        formula="各期市場總量（優先取來源報表總計列，否則為各機構加總）",
        notes=notes,
        evidence=evidence,
    )


def derive_top(
    metric: MetricSeries,
    n: int = DEFAULT_TOP_N,
    *,
    by: str | None = None,
) -> MetricSeries:
    """
    Top N 切片：只留下最大的 N 個類別，數值原封不動搬過來。

    為什麼需要這個：金管會月報有 33 家機構。33 個扇形的圓餅圖沒有人看得懂
    （chart_planner 的 ``MAX_PIE_CATEGORIES`` 會擋），33 列的表格也塞不進
    一頁（``MAX_TABLE_ROWS`` 會擋）。附件三多頁都是「Top 10 銀行」，這是
    顧問簡報的標準做法。

    **這裡不重新計算任何數值**，只是選取子集。這點很要緊：市占率若在切片後
    重算，分母會變成「Top 10 的總和」，每家的市占率都會被高估。所以 share
    指標切片後仍是對全市場的占比，與切片前完全相同。

    Args:
        metric: 任何橫斷面指標（``.value``／``.share`` 皆可）。
        n: 取前幾名。
        by: 依哪一個系列排序，預設取最後一個系列（通常是最新一期）。

    Returns:
        新的 :class:`MetricSeries`，metric_key 為原鍵加上 ``.top{n}``。
        不適用時回傳標記 ``computable=False`` 的佔位指標。
    """
    metric_key = f"{metric.metric_key}.top{n}"

    def blocked(reasons: list[str]) -> MetricSeries:
        return MetricSeries(
            metric_key=metric_key,
            name=f"{metric.name} Top {n}",
            categories=list(metric.categories),
            series={},
            unit=metric.unit,
            semantic=metric.semantic,
            value_semantic=metric.value_semantic,
            aggregation_semantic=metric.aggregation_semantic,
            allowed_derivations=list(metric.allowed_derivations),
            shape_kind=metric.shape_kind,
            axis_kind=metric.axis_kind,
            computable=False,
            notes=reasons,
        )

    if "top" not in metric.allowed_derivations:
        return blocked(
            [f"measure semantic={metric.value_semantic} 未核准 Top N 推導"]
        )

    # 對月份取「前 10 名」不具商業意義——時間軸的順序本身就是資訊，
    # 重排之後折線圖會變成一條沒有意義的鋸齒。
    if metric.axis_kind == AXIS_TEMPORAL:
        return blocked(["類別軸為時間序列，Top N 排序會破壞時間順序，不予計算"])

    if not metric.series:
        return blocked(["原指標沒有任何可用系列"])

    ranking_series = by or metric.series_names[-1]

    if ranking_series not in metric.series:
        return blocked(
            [f"排序依據系列 {ranking_series!r} 不存在於原指標"]
        )

    # 合計列排除（見 is_total_category）——它一定最大，會佔掉一個名額。
    candidates = [
        (index, value)
        for index, (label, value) in enumerate(
            zip(metric.categories, metric.series[ranking_series])
        )
        if value is not None and not is_total_category(label)
    ]

    if len(candidates) <= n:
        return blocked(
            [
                f"可排序的類別只有 {len(candidates)} 個，未超過 {n}，"
                "無需切片（請直接使用原指標）"
            ]
        )

    kept = sorted(candidates, key=lambda item: item[1], reverse=True)[:n]
    indices = [index for index, _ in kept]

    return MetricSeries(
        metric_key=metric_key,
        name=f"{metric.name} Top {n}",
        categories=[metric.categories[index] for index in indices],
        series={
            name: [values[index] for index in indices]
            for name, values in metric.series.items()
        },
        unit=metric.unit,
        series_units={
            name: metric.unit_for(name) for name in metric.series_names
        },
        semantic=metric.semantic,
        value_semantic=metric.value_semantic,
        aggregation_semantic=metric.aggregation_semantic,
        allowed_derivations=list(metric.allowed_derivations),
        shape_kind=metric.shape_kind,
        axis_kind=metric.axis_kind,
        formula=(
            f"依「{ranking_series}」由大到小取前 {n} 名"
            f"（排除合計列）；數值沿用原指標，未重新計算"
        ),
        notes=[
            f"僅呈現前 {n} 名，非全部 "
            f"{len([1 for label in metric.categories if not is_total_category(label)])}"
            " 個類別",
        ],
        evidence={
            key: ref
            for key, ref in metric.evidence.items()
            if key.split("|", 1)[-1]
            in {metric.categories[index] for index in indices}
        },
        requires_human_review=metric.requires_human_review,
    )


def derive_forecast(base: MetricSeries, periods: int = 3) -> MetricSeries:
    """
    線性趨勢外推。

    使用最小平方法線性回歸（scikit-learn 不可用時回退 numpy polyfit），
    外推結果的類別標記為「預測」，避免與實際值混淆。
    """
    metric_key = base.metric_key.replace(".value", ".forecast")

    if "forecast" not in base.allowed_derivations:
        return _blocked_metric(
            base,
            ".forecast",
            "趨勢外推",
            base.unit,
            "forecast",
            [f"measure semantic={base.value_semantic} 未核准趨勢外推"],
        )

    # 外推只在時間軸上有意義。把銀行名稱當 x 軸做回歸會得到
    # 「預測+1 期的銀行卡數」這種無意義甚至負值的結果。
    if base.axis_kind != AXIS_TEMPORAL:
        return _blocked_metric(
            base,
            ".forecast",
            "趨勢外推",
            base.unit,
            "forecast",
            ["類別軸為橫斷面分類（非時間序列），趨勢外推無意義，不予計算"],
        )

    usable = {
        name: values
        for name, values in base.series.items()
        if sum(1 for value in values if value is not None) >= MIN_POINTS_FOR_FORECAST
    }

    if not usable:
        return _blocked_metric(
            base,
            ".forecast",
            "趨勢外推",
            base.unit,
            "forecast",
            [f"沒有任何系列達到外推所需的 {MIN_POINTS_FOR_FORECAST} 個歷史點"],
        )

    categories = list(base.categories) + [
        f"預測+{offset}" for offset in range(1, periods + 1)
    ]

    forecast: dict[str, list[float | None]] = {}
    notes = [f"後 {periods} 期為模型外推值，非實際觀測值"]

    for name, values in usable.items():
        points = [
            (index, value)
            for index, value in enumerate(values)
            if value is not None
        ]

        slope, intercept = _fit_linear(points)

        row: list[float | None] = list(values)

        for offset in range(1, periods + 1):
            x = len(values) - 1 + offset
            row.append(_round(slope * x + intercept))

        # 歷史值皆非負卻外推出負值，代表線性模型已外推過遠。
        # 不靜默截斷（那會變成編造數字），改為明確警示。
        historical = [value for value in values if value is not None]

        if all(value >= 0 for value in historical) and any(
            value is not None and value < 0 for value in row[len(values) :]
        ):
            notes.append(
                f"系列 {name} 的線性外推出現負值，"
                "歷史資料皆為非負，建議縮短外推期數或改用其他模型"
            )

        forecast[name] = row

    return MetricSeries(
        metric_key=metric_key,
        name=f"{base.name} 趨勢外推",
        categories=categories,
        series=forecast,
        unit=base.unit,
        series_units={name: base.unit_for(name) for name in forecast},
        semantic="forecast",
        value_semantic=base.value_semantic,
        aggregation_semantic=base.aggregation_semantic,
        allowed_derivations=list(base.allowed_derivations),
        shape_kind=base.shape_kind,
        axis_kind=base.axis_kind,
        formula="最小平方法線性回歸 y = ax + b，外推後續期數",
        notes=notes,
        requires_human_review=base.requires_human_review,
    )


def _fit_linear(points: Sequence[tuple[int, float]]) -> tuple[float, float]:
    """回傳 (斜率, 截距)。優先用 scikit-learn，缺套件時回退 numpy。"""
    xs = [float(x) for x, _ in points]
    ys = [float(y) for _, y in points]

    try:
        from sklearn.linear_model import LinearRegression

        model = LinearRegression()
        model.fit([[x] for x in xs], ys)
        return float(model.coef_[0]), float(model.intercept_)
    except ImportError:
        import numpy as np

        slope, intercept = np.polyfit(xs, ys, 1)
        return float(slope), float(intercept)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def build_metric_store(
    load_result: LoadResult,
    config: EngineConfig | None = None,
) -> tuple[MetricStore, EngineReport]:
    """Build the single MetricStore through deterministic profiled rules."""
    settings = config or EngineConfig()
    store = MetricStore(source_files=list(load_result.source_files))
    report = EngineReport()

    for dataset in load_result.datasets:
        profile = profile_dataset(dataset)
        report.dataset_profiles.append(profile.model_dump(mode="json"))
        report.notes.extend(profile.warnings)
        bases = build_base_metrics(dataset, profile)

        if not bases:
            report.notes.append(
                f"資料集 {dataset.dataset_id} 沒有數值欄位，已跳過"
            )
            continue

        for base in bases:
            store.add(base)
            derivations: list[MetricSeries] = []

            if settings.enable_period_growth:
                derivations.append(derive_period_growth(base))
            if settings.enable_yoy:
                derivations.append(derive_yoy(base))
            if settings.enable_share:
                derivations.append(derive_share(base))
            if settings.enable_rank:
                derivations.append(derive_rank(base))
            if settings.enable_forecast:
                derivations.append(
                    derive_forecast(base, settings.forecast_periods)
                )

            for derived in derivations:
                store.add(derived)

            if settings.enable_top_n:
                for source in [base, *derivations]:
                    if source.semantic not in {"value", "share"}:
                        continue
                    if not source.computable:
                        continue
                    store.add(derive_top(source, settings.top_n))

    if settings.enable_market_timeline:
        timeline = build_market_timeline(
            [
                metric
                for metric in store.metrics.values()
                if metric.metric_key.endswith(".value")
            ]
        )

        if timeline is None:
            report.notes.append(
                "資料中沒有可安全加總的 entity × period 視圖，"
                "未建立跨實體期間彙總指標"
            )
        else:
            store.add(timeline)
            for derived in (
                derive_period_growth(timeline),
                derive_yoy(timeline),
                derive_forecast(timeline, settings.forecast_periods),
            ):
                store.add(derived)

    for dataset_id, reason in load_result.skipped.items():
        report.notes.append(f"資料集 {dataset_id} 未納入：{reason}")

    report.metric_count = len(store.computable_metric_keys())
    report.blocked = store.blocked_metrics()
    return store, report


def build_metric_store_from_contract(
    ingestion_payload: dict[str, Any],
) -> dict[str, Any]:
    """JSON-only deterministic engine boundary for normalized ingestion."""
    from ..contracts import stages as stage_contracts
    from . import dataset_loader

    loaded = dataset_loader.load_ingestion_result(ingestion_payload)
    store, report = build_metric_store(loaded)
    return stage_contracts.metric_engine_result_payload(
        {
            "dataset_ids": [dataset.dataset_id for dataset in loaded.datasets],
            "engine_report": {
                "metric_count": report.metric_count,
                "blocked": report.blocked,
                "notes": report.notes,
                "dataset_profiles": report.dataset_profiles,
            },
            "metric_store": stage_contracts.metric_store_payload(
                store.to_dict()
            ),
        }
    )