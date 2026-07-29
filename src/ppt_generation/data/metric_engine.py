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
from typing import Sequence

import pandas as pd

from .dataset_loader import ColumnMeta, LoadedDataset, LoadResult
from .metric_store import MetricSeries, MetricStore, SourceRef


#: 計算成長率所需的最少期數。
MIN_PERIODS_FOR_GROWTH = 2

#: 線性外推所需的最少歷史點數。
MIN_POINTS_FOR_FORECAST = 4

#: 數值四捨五入位數。避免浮點誤差讓三方比對出現尾差。
ROUND_DIGITS = 4


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
    #: 是否做趨勢外推
    enable_forecast: bool = False
    #: 外推期數
    forecast_periods: int = 3


@dataclass
class EngineReport:
    """引擎執行摘要，供回報使用者與稽核。"""

    metric_count: int = 0
    blocked: dict[str, list[str]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
_YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

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
)

#: 類別軸語意類型。
AXIS_TEMPORAL = "temporal"
AXIS_CATEGORICAL = "categorical"


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
    return int(match.group()) if match else None


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
def build_base_metric(dataset: LoadedDataset) -> MetricSeries | None:
    """
    把一個資料集直接轉為一個「原始值」指標。

    類別軸取第一個非數值欄位，系列取所有數值欄位。
    這是所有衍生指標的計算基礎。
    """
    numeric_columns = sorted(dataset.numeric_columns(), key=lambda m: m.index)

    if not numeric_columns:
        return None

    category_column = _category_column(dataset)
    categories = _categories_of(dataset, category_column)

    series = {
        meta.label: _clean_values(dataset.frame[meta.key].tolist())
        for meta in numeric_columns
    }

    units = {meta.unit for meta in numeric_columns if meta.unit}

    return MetricSeries(
        metric_key=f"{_slugify(dataset.dataset_id)}.value",
        name=dataset.name,
        categories=categories,
        series=series,
        # 各欄位單位不一致時不猜測，留空由敘事層依欄位名說明。
        unit=units.pop() if len(units) == 1 else None,
        semantic="value",
        axis_kind=detect_axis_kind(categories, category_column),
        evidence=_evidence_map(dataset, numeric_columns, categories),
        requires_human_review=dataset.requires_human_review,
        notes=list(dataset.warnings),
    )


# ---------------------------------------------------------------------------
# 衍生指標
# ---------------------------------------------------------------------------
def derive_period_growth(base: MetricSeries) -> MetricSeries:
    """
    期間成長率（相鄰類別之間，對應 MoM／QoQ）。

    第一期沒有前期可比，值為 None —— 不編造基期，也不補 0。
    """
    metric_key = base.metric_key.replace(".value", ".period_growth")

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
        axis_kind=base.axis_kind,
        formula="(當年 - 去年) / 去年 × 100%",
        requires_human_review=base.requires_human_review,
    )


def derive_share(base: MetricSeries) -> MetricSeries:
    """
    占比／市占率：每個類別占該系列總和的百分比。

    僅在數值全為非負時才計算 —— 含負值的資料算占比沒有商業意義
    （例如損益表的虧損項），此時標記不可計算。
    """
    metric_key = base.metric_key.replace(".value", ".share")

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

    for series_name, values in base.series.items():
        present = [value for value in values if value is not None]

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
            None if value is None else _round(value / total * 100)
            for value in values
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
        formula="各類別值 / 該系列總和 × 100%",
        notes=blocked_reasons,
        requires_human_review=base.requires_human_review,
    )


def derive_rank(base: MetricSeries) -> MetricSeries:
    """
    排名：每個系列內由大到小排名（1 為最大）。

    類別數少於 2 時無排名意義，標記不可計算。
    """
    metric_key = base.metric_key.replace(".value", ".rank")

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

    for series_name, values in base.series.items():
        column = pd.Series(values, dtype="float64")
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
        formula="同系列內由大到小排序，並列取較小名次",
        requires_human_review=base.requires_human_review,
    )


def derive_forecast(base: MetricSeries, periods: int = 3) -> MetricSeries:
    """
    線性趨勢外推。

    使用最小平方法線性回歸（scikit-learn 不可用時回退 numpy polyfit），
    外推結果的類別標記為「預測」，避免與實際值混淆。
    """
    metric_key = base.metric_key.replace(".value", ".forecast")

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
        semantic="forecast",
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
    """
    從已載入的資料集建立 MetricStore。

    對每個資料集先建立 ``.value`` 基礎指標，再依設定衍生
    ``.period_growth`` / ``.yoy`` / ``.share`` / ``.rank`` / ``.forecast``。

    Returns:
        (MetricStore, EngineReport)。Report 含被防呆擋下的指標與原因，
        呼叫端應把這些原因回報給使用者，而非靜默忽略。
    """
    settings = config or EngineConfig()
    store = MetricStore(source_files=list(load_result.source_files))
    report = EngineReport()

    for dataset in load_result.datasets:
        base = build_base_metric(dataset)

        if base is None:
            report.notes.append(
                f"資料集 {dataset.dataset_id} 沒有數值欄位，已跳過"
            )
            continue

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
            derivations.append(derive_forecast(base, settings.forecast_periods))

        for derived in derivations:
            store.add(derived)

    for dataset_id, reason in load_result.skipped.items():
        report.notes.append(f"資料集 {dataset_id} 未納入：{reason}")

    report.metric_count = len(store.computable_metric_keys())
    report.blocked = store.blocked_metrics()

    return store, report
