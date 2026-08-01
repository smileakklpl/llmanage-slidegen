"""
敘事佔位符解析與代入
=====================
對應 docs/圖表原生性與資料同步設計.md §4.2 的 NarrativeWriterAgent 限制：

> 輸出文字中的數字只能以 ``{{metric_key}}`` 佔位符表示，禁止輸出字面數值；
> renderer 於最終組裝時才代入實際值。

佔位符語法::

    {{metric_key|series_name|selector}}

- ``metric_key``：MetricStore 中的指標鍵（必填）
- ``series_name``：系列名稱。省略時，若該指標只有一組系列則自動採用
- ``selector``：取值方式，可為類別名稱（如 ``3月``）或以下彙總關鍵字：

  | selector | 意義 |
  |---|---|
  | ``latest`` | 最後一個非空值（預設） |
  | ``first`` | 第一個非空值 |
  | ``max`` / ``min`` | 最大／最小值 |
  | ``sum`` / ``avg`` | 總和／平均 |
  | ``max_category`` / ``min_category`` | 最大／最小值所在的類別名稱 |

範例::

    "市場流通卡數達 {{mkt.value|2026年|latest}}，年增 {{mkt.yoy|2026 vs 2025|latest}}"
    "龍頭為 {{bank.share|市占率|max_category}}，市占 {{bank.share|市占率|max}}"

所有數字都在此模組由 MetricStore 查表產生，LLM 只能寫佔位符本身。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ..data.metric_engine import is_total_category
from ..data.metric_store import (
    MetricNotComputableError,
    MetricNotFoundError,
    MetricSeries,
    MetricStore,
)


#: 佔位符樣式。非貪婪比對，避免相鄰兩個佔位符被吃成一個。
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(.+?)\s*\}\}")

#: 彙總關鍵字 → 是否回傳類別名稱（而非數值）。
AGGREGATE_SELECTORS: dict[str, bool] = {
    "latest": False,
    "first": False,
    "max": False,
    "min": False,
    "sum": False,
    "avg": False,
    "max_category": True,
    "min_category": True,
}

#: 偵測「裸數字」用。Reviewer 以此攔截 LLM 直接寫死數值的情況。
#: 會忽略佔位符內部的內容（呼叫端先把佔位符挖掉再比對）。
BARE_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z_])"          # 不是識別字的一部分
    r"\d+(?:,\d{3})*(?:\.\d+)?"  # 1234 / 1,234 / 12.34
    r"\s*(?:%|％|百分點|萬|億|千|元|張|人|倍)?"
)

#: 允許出現在敘事中的裸數字白名單（年份、季度、頁碼等結構性數字）。
_ALLOWED_BARE_PATTERNS = (
    re.compile(r"^(19|20)\d{2}$"),          # 年份
    re.compile(r"^[1-9]\d{0,2}$"),          # 小整數（月份、季、Top N、頁碼）
    re.compile(r"^(19|20)\d{2}\s*年$"),
)


class PlaceholderError(ValueError):
    """佔位符無法解析或查表失敗。"""


@dataclass(frozen=True)
class Placeholder:
    """一個已解析的佔位符。"""

    raw: str
    metric_key: str
    series_name: str | None = None
    selector: str = "latest"

    @property
    def is_aggregate(self) -> bool:
        return self.selector in AGGREGATE_SELECTORS

    @property
    def returns_category(self) -> bool:
        return AGGREGATE_SELECTORS.get(self.selector, False)


def parse_placeholder(raw: str) -> Placeholder:
    """
    解析單一佔位符內容（不含外層大括號）。

    Raises:
        PlaceholderError: 格式不合法。
    """
    parts = [part.strip() for part in raw.split("|")]

    if not parts or not parts[0]:
        raise PlaceholderError(f"佔位符 {{{{{raw}}}}} 缺少 metric_key")

    if len(parts) > 3:
        raise PlaceholderError(
            f"佔位符 {{{{{raw}}}}} 欄位過多，格式應為 "
            "metric_key|series_name|selector"
        )

    metric_key = parts[0]
    series_name = parts[1] if len(parts) > 1 and parts[1] else None
    selector = parts[2] if len(parts) > 2 and parts[2] else "latest"

    return Placeholder(
        raw=raw,
        metric_key=metric_key,
        series_name=series_name,
        selector=selector,
    )


def parse_placeholders(text: str) -> list[Placeholder]:
    """取出文字中所有佔位符。解析失敗的項目會拋錯。"""
    return [
        parse_placeholder(match.group(1))
        for match in PLACEHOLDER_PATTERN.finditer(text)
    ]


def strip_placeholders(text: str) -> str:
    """把佔位符整段移除，供裸數字偵測使用。"""
    return PLACEHOLDER_PATTERN.sub(" ", text)


# ---------------------------------------------------------------------------
# 數值格式化
# ---------------------------------------------------------------------------
def format_value(value: float, unit: str | None = None) -> str:
    """
    把數值格式化為簡報可讀形式。

    百分比與名次沿用整數或一位小數；一般數值加千分位。
    刻意不做任何單位換算（如萬 → 億），避免產生與來源 Excel 不同的數字。
    """
    if unit in {"%", "％"}:
        return f"{value:,.1f}%"

    if unit == "名":
        return f"第 {int(round(value))} 名"

    if float(value).is_integer():
        formatted = f"{int(round(value)):,}"
    else:
        formatted = f"{value:,.1f}"

    return f"{formatted}{unit}" if unit else formatted


# ---------------------------------------------------------------------------
# 查表
# ---------------------------------------------------------------------------
def _resolve_series_name(
    placeholder: Placeholder,
    metric: MetricSeries,
) -> str:
    if placeholder.series_name is not None:
        if placeholder.series_name not in metric.series:
            raise PlaceholderError(
                f"佔位符 {{{{{placeholder.raw}}}}} 指定的系列 "
                f"{placeholder.series_name!r} 不存在，"
                f"可用系列：{metric.series_names}"
            )

        return placeholder.series_name

    names = metric.series_names

    if len(names) != 1:
        raise PlaceholderError(
            f"指標 {placeholder.metric_key!r} 有多組系列 {names}，"
            f"佔位符 {{{{{placeholder.raw}}}}} 必須明確指定 series_name"
        )

    return names[0]


def _present_pairs(
    categories: Sequence[str],
    values: Sequence[float | None],
) -> list[tuple[str, float]]:
    """取出有值的 (類別, 數值) 配對，過濾 None 與 NaN。"""
    pairs: list[tuple[str, float]] = []

    for category, value in zip(categories, values):
        if value is None:
            continue

        if isinstance(value, float) and math.isnan(value):
            continue

        pairs.append((category, float(value)))

    return pairs


def resolve_placeholder(
    placeholder: Placeholder,
    store: MetricStore,
) -> str:
    """
    查表並回傳已格式化的字串。

    Raises:
        PlaceholderError: 指標不存在、被防呆擋下，或選取條件無對應資料。
    """
    try:
        metric = store.get(placeholder.metric_key)
    except MetricNotFoundError as error:
        raise PlaceholderError(
            f"佔位符 {{{{{placeholder.raw}}}}} 引用了不存在的指標："
            f"{error}"
        ) from error
    except MetricNotComputableError as error:
        raise PlaceholderError(
            f"佔位符 {{{{{placeholder.raw}}}}} 引用了不可用的指標：{error}"
        ) from error

    series_name = _resolve_series_name(placeholder, metric)
    series_unit = metric.unit_for(series_name)
    values = metric.values_for(series_name)
    pairs = _present_pairs(metric.categories, values)

    if not pairs:
        raise PlaceholderError(
            f"指標 {placeholder.metric_key!r} 的系列 {series_name!r} "
            "沒有任何可用數值"
        )

    selector = placeholder.selector

    # 類別名稱優先於彙總關鍵字：若使用者資料中真有一欄叫 "max"，
    # 以實際類別為準，避免誤判。明確選取「總計」時回傳官方總量數值；
    # 只有回傳實體名稱或做機構比較的 selector 才排除合計列。
    if selector in metric.categories:
        index = metric.categories.index(selector)
        value = values[index]

        if value is None:
            raise PlaceholderError(
                f"指標 {placeholder.metric_key!r} 系列 {series_name!r} "
                f"在類別 {selector!r} 沒有數值"
            )

        return format_value(value, series_unit)

    if selector not in AGGREGATE_SELECTORS:
        raise PlaceholderError(
            f"佔位符 {{{{{placeholder.raw}}}}} 的 selector {selector!r} "
            f"既不是類別名稱，也不是彙總關鍵字。"
            f"可用類別：{list(metric.categories)}；"
            f"可用關鍵字：{sorted(AGGREGATE_SELECTORS)}"
        )

    aggregate_pairs = pairs
    official_total: float | None = None

    if metric.axis_kind == "categorical":
        total_pairs = [pair for pair in pairs if is_total_category(pair[0])]
        entity_pairs = [pair for pair in pairs if not is_total_category(pair[0])]

        if total_pairs:
            # 來源總計可能包含未逐家揭露機構，權威性高於自行加總。
            official_total = total_pairs[0][1]

        aggregate_pairs = entity_pairs

        if not aggregate_pairs and not (
            selector == "sum" and official_total is not None
        ):
            raise PlaceholderError(
                f"指標 {placeholder.metric_key!r} 排除合計列後沒有可用實體"
            )

    if selector == "latest":
        return format_value(aggregate_pairs[-1][1], series_unit)

    if selector == "first":
        return format_value(aggregate_pairs[0][1], series_unit)

    if selector == "max":
        return format_value(
            max(value for _, value in aggregate_pairs), series_unit
        )

    if selector == "min":
        return format_value(
            min(value for _, value in aggregate_pairs), series_unit
        )

    if selector == "sum":
        total = (
            official_total
            if official_total is not None
            else sum(value for _, value in aggregate_pairs)
        )
        return format_value(total, series_unit)

    if selector == "avg":
        total = sum(value for _, value in aggregate_pairs)
        return format_value(total / len(aggregate_pairs), series_unit)

    if selector == "max_category":
        return max(aggregate_pairs, key=lambda pair: pair[1])[0]

    if selector == "min_category":
        return min(aggregate_pairs, key=lambda pair: pair[1])[0]

    # AGGREGATE_SELECTORS 已窮舉，理論上不會到這裡。
    raise PlaceholderError(f"未實作的 selector：{selector!r}")


def render_text(
    text: str,
    store: MetricStore,
    *,
    strict: bool = True,
) -> tuple[str, list[str]]:
    """
    把文字中的佔位符全部代入實際數值。

    Args:
        text: 含佔位符的敘事文字。
        store: 唯一真相來源。
        strict: True 時任一佔位符失敗即拋錯；False 時保留原佔位符並收集錯誤。

    Returns:
        (已代入的文字, 錯誤訊息清單)。

    Raises:
        PlaceholderError: ``strict=True`` 且有佔位符無法解析。
    """
    errors: list[str] = []

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1)

        try:
            placeholder = parse_placeholder(raw)
            return resolve_placeholder(placeholder, store)
        except PlaceholderError as error:
            if strict:
                raise

            errors.append(str(error))
            return match.group(0)

    rendered = PLACEHOLDER_PATTERN.sub(replace, text)
    return rendered, errors


@dataclass(frozen=True)
class TextSegment:
    """
    代入後的一段文字，並記錄它是不是查表得來的值。

    renderer 依 ``from_metric`` 決定要不要加黃色標示：被標到的每個字元
    都來自 MetricStore，這讓「哪些字是系統算的、哪些是敘事」在簡報上
    肉眼可辨，也是 FR-3.4 溯源的第一層線索。
    """

    text: str
    from_metric: bool = False
    #: 來源指標鍵（``from_metric`` 為 True 時才有值），供溯源使用。
    metric_key: str | None = None


def render_segments(
    text: str,
    store: MetricStore,
    *,
    strict: bool = False,
) -> tuple[list[TextSegment], list[str]]:
    """
    與 :func:`render_text` 相同的代入邏輯，但保留「哪一段是值」的資訊。

    Returns:
        (片段清單, 錯誤訊息清單)。串接所有片段的 ``text`` 等於
        :func:`render_text` 的輸出——兩者共用同一組解析與查表函式，
        不會出現兩種代入結果。
    """
    segments: list[TextSegment] = []
    errors: list[str] = []
    cursor = 0

    def push_literal(chunk: str) -> None:
        if chunk:
            segments.append(TextSegment(chunk))

    for match in PLACEHOLDER_PATTERN.finditer(text):
        push_literal(text[cursor : match.start()])
        cursor = match.end()
        raw = match.group(1)

        try:
            placeholder = parse_placeholder(raw)
            value = resolve_placeholder(placeholder, store)
        except PlaceholderError as error:
            if strict:
                raise

            errors.append(str(error))
            # 代入失敗時保留原佔位符，讓錯誤在簡報上看得見而不是靜默消失。
            push_literal(match.group(0))
            continue

        segments.append(
            TextSegment(value, from_metric=True, metric_key=placeholder.metric_key)
        )

    push_literal(text[cursor:])

    return segments, errors


# ---------------------------------------------------------------------------
# 裸數字偵測（Reviewer 規則層）
# ---------------------------------------------------------------------------
def find_bare_numbers(text: str) -> list[str]:
    """
    找出敘事中未經佔位符引用的裸數字。

    年份、月份、Top N 等結構性小整數屬白名單，不視為違規；
    「市占率 34.5%」這種指標數值則必須改用佔位符。
    """
    stripped = strip_placeholders(text)
    violations: list[str] = []

    for match in BARE_NUMBER_PATTERN.finditer(stripped):
        candidate = match.group().strip()

        if not candidate:
            continue

        if any(pattern.match(candidate) for pattern in _ALLOWED_BARE_PATTERNS):
            continue

        violations.append(candidate)

    return violations


def cited_metric_keys(text: str) -> list[str]:
    """取出文字中所有被引用的 metric_key（去重，保留出現順序）。"""
    seen: dict[str, None] = {}

    for match in PLACEHOLDER_PATTERN.finditer(text):
        try:
            placeholder = parse_placeholder(match.group(1))
        except PlaceholderError:
            continue

        seen.setdefault(placeholder.metric_key, None)

    return list(seen)


def describe_available_placeholders(store: MetricStore) -> list[dict[str, Any]]:
    """
    產生給 NarrativeWriterAgent 的佔位符使用說明。

    只列 metric_key、系列名稱、類別名稱與可用關鍵字，**不含任何數值**。
    """
    described: list[dict[str, Any]] = []

    for metric_key in store.computable_metric_keys():
        metric = store.get(metric_key)

        described.append(
            {
                "metric_key": metric_key,
                "name": metric.name,
                "unit": metric.unit,
                "series_units": {
                    name: metric.unit_for(name) for name in metric.series_names
                },
                "series_names": metric.series_names,
                "categories": list(metric.categories),
                "example": (
                    f"{{{{{metric_key}|{metric.series_names[0]}|latest}}}}"
                    if metric.series_names
                    else None
                ),
            }
        )

    return described
