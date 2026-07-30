"""
ChartPlan 驗證與查表組裝
========================
對應 docs/圖表原生性與資料同步設計.md §4.4。

這是 LLM 圖表決策與實際圖表生成之間的**唯一橋樑**，也是防呆關卡：

    LLM → ChartPlan（只有 metric_key 引用，無數字）
            │
            ▼  validate_chart_plan()  ← 確定性檢查，不呼叫 LLM
            │
            ▼  resolve_chart_plan()   ← 從 MetricStore 查表取數字
            │
            ▼  ChartSpec / ScatterSpec → chart_builder.add_chart()

`ChartPlan` 刻意不含任何數值欄位，因此即使 LLM 想編造數字也無處可放；
所有數字都是在 :func:`resolve_chart_plan` 這一步由本地程式查表填入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from ..data.metric_engine import AXIS_TEMPORAL, is_total_category
from ..data.metric_store import (
    MetricNotComputableError,
    MetricNotFoundError,
    MetricSeries,
    MetricStore,
)
from .chart_builder import (
    CHART_SKILLS,
    CHART_TYPE_BY_SKILL,
    ChartSpec,
    ComboSpec,
    ScatterSpec,
)
from .table_builder import (
    MAX_TABLE_ROWS,
    TABLE_SKILLS,
    TableSpec,
)


ChartType = Literal[
    "column", "bar", "line", "pie", "scatter", "combo", "table", "heatmap"
]

#: 圖表與表格的合併 registry。planner 對兩者做同一套防呆與查表，
#: 差別只在最後產出的 spec 型別與落版的 API（add_chart / add_table）。
VISUAL_SKILLS = {**CHART_SKILLS, **TABLE_SKILLS}

#: 走原生表格而非原生圖表的 skill。這些沒有內嵌 workbook，
#: 三方比對改以儲存格文字為①（見 verification 模組）。
TABLE_LIKE_CHARTS = frozenset(TABLE_SKILLS)

#: 只允許在時間序列軸上使用的圖表類型。
#: 折線圖隱含「連續變化」語意，用在銀行名稱這種橫斷面軸上會誤導讀者。
TEMPORAL_ONLY_CHARTS = frozenset({"line"})

#: 不允許用於時間序列軸的圖表類型。
#: 圓餅圖表達「組成比例」，各月份占全年比例不是有意義的組成。
CATEGORICAL_ONLY_CHARTS = frozenset({"pie"})

#: 圓餅圖類別數上限。超過此數量的圓餅圖無法閱讀，應改用橫條圖。
MAX_PIE_CATEGORIES = 12


class ChartPlanError(ValueError):
    """ChartPlan 未通過防呆檢查。訊息會回饋給 LLM 供重試。"""


@dataclass
class ChartPlan:
    """
    LLM 唯一允許輸出的圖表決策格式。

    注意此結構**完全沒有數值欄位**，這是刻意設計，逼迫 LLM 只能做
    「選擇」而不能做「計算」或「編造」。
    """

    slide_title: str
    chart_type: ChartType
    chart_title: str
    metric_key: str
    series_names: list[str] | None = None
    #: 該頁在簡報中的頁碼（由 Orchestrator 指派，非 LLM 決定）
    page_number: int | None = None

    @classmethod
    def from_tool_call(
        cls,
        skill_name: str,
        arguments: dict[str, Any],
        *,
        slide_title: str | None = None,
        page_number: int | None = None,
    ) -> ChartPlan:
        """
        由 :func:`llm_client.complete_tool_call` 的結果組裝 ChartPlan。

        ``skill_name`` 即 chart_type；arguments 內只會有 metric_key、
        series_names、chart_title（見 CHART_SKILL_TOOL_SCHEMAS）。
        """
        chart_title = arguments.get("chart_title") or ""
        series_names = arguments.get("series_names")

        return cls(
            slide_title=slide_title or chart_title,
            chart_type=skill_name,  # type: ignore[arg-type]
            chart_title=chart_title,
            metric_key=arguments.get("metric_key", ""),
            series_names=list(series_names) if series_names else None,
            page_number=page_number,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "slide_title": self.slide_title,
            "chart_type": self.chart_type,
            "chart_title": self.chart_title,
            "metric_key": self.metric_key,
            "series_names": list(self.series_names) if self.series_names else None,
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ChartPlan:
        series_names = payload.get("series_names")

        return cls(
            slide_title=payload.get("slide_title", ""),
            chart_type=payload.get("chart_type", "column"),
            chart_title=payload.get("chart_title", ""),
            metric_key=payload.get("metric_key", ""),
            series_names=list(series_names) if series_names else None,
            page_number=payload.get("page_number"),
        )


@dataclass
class ResolvedChart:
    """
    已查表完成的圖表，可直接交給 chart_builder 生成。

    ``spec`` 是 ChartSpec 或 ScatterSpec；``skill_name`` 決定要呼叫
    CHART_SKILLS 中的哪個函式。
    """

    plan: ChartPlan
    skill_name: str
    spec: ChartSpec | ScatterSpec
    metric: MetricSeries
    #: 實際採用的系列名稱（plan 未指定時為該指標的全部系列）
    series_names: list[str] = field(default_factory=list)

    @property
    def is_scatter(self) -> bool:
        return isinstance(self.spec, ScatterSpec)

    @property
    def is_table(self) -> bool:
        """True 代表落版走 ``add_table()``，沒有內嵌 workbook 可供右鍵編輯。"""
        return isinstance(self.spec, TableSpec)


# ---------------------------------------------------------------------------
# 驗證
# ---------------------------------------------------------------------------
def validate_chart_plan(
    plan: ChartPlan,
    store: MetricStore,
) -> list[str]:
    """
    確定性檢查 ChartPlan 是否可執行。不呼叫 LLM。

    檢查項目：
    1. chart_type 是否為已註冊的 skill
    2. metric_key 是否存在於 MetricStore 且通過防呆（computable）
    3. series_names 是否都存在於該指標下
    4. 圖表類型是否與指標的軸語意相容（折線不用在橫斷面、圓餅不用在時間軸）
    5. 圖表類型特有限制（圓餅單一系列且類別數合理、散點需兩個系列）

    Returns:
        錯誤訊息清單。空清單代表通過。
        訊息刻意寫成可直接回饋給 LLM 重試的形式。
    """
    errors: list[str] = []

    if plan.chart_type not in VISUAL_SKILLS:
        errors.append(
            f"chart_type {plan.chart_type!r} 不是已註冊的圖表 skill，"
            f"可用選項：{sorted(VISUAL_SKILLS)}"
        )
        # 圖表類型不合法時，後續類型相關檢查無從進行。
        return errors

    if not plan.metric_key:
        errors.append("metric_key 不可為空")
        return errors

    try:
        metric = store.get(plan.metric_key)
    except MetricNotFoundError:
        errors.append(
            f"metric_key {plan.metric_key!r} 不存在於 MetricStore。"
            f"可用指標：{store.computable_metric_keys()}"
        )
        return errors
    except MetricNotComputableError as error:
        errors.append(str(error))
        return errors

    selected = plan.series_names or metric.series_names

    unknown = [name for name in selected if name not in metric.series]

    if unknown:
        errors.append(
            f"指標 {plan.metric_key!r} 中不存在系列 {unknown}，"
            f"可用系列：{metric.series_names}"
        )

    if not selected:
        errors.append(f"指標 {plan.metric_key!r} 沒有任何可用系列")
        return errors

    errors.extend(_validate_axis_compatibility(plan, metric))
    errors.extend(_validate_chart_type_limits(plan, metric, selected))

    return errors


def _validate_axis_compatibility(
    plan: ChartPlan,
    metric: MetricSeries,
) -> list[str]:
    """檢查圖表類型與類別軸語意是否相容。"""
    errors: list[str] = []
    is_temporal = metric.axis_kind == "temporal"

    if plan.chart_type in TEMPORAL_ONLY_CHARTS and not is_temporal:
        errors.append(
            f"{plan.chart_type} 圖隱含連續變化語意，只能用於時間序列指標，"
            f"但 {plan.metric_key!r} 的類別軸是橫斷面分類"
            f"（{metric.categories[:3]}...）。建議改用 column 或 bar。"
        )

    if plan.chart_type in CATEGORICAL_ONLY_CHARTS and is_temporal:
        errors.append(
            f"{plan.chart_type} 圖表達組成比例，不適用於時間序列指標 "
            f"{plan.metric_key!r}。建議改用 column 或 line。"
        )

    return errors


def _validate_chart_type_limits(
    plan: ChartPlan,
    metric: MetricSeries,
    selected: Sequence[str],
) -> list[str]:
    """檢查各圖表類型特有的資料形狀限制。"""
    errors: list[str] = []

    if plan.chart_type == "pie":
        if len(selected) != 1:
            errors.append(
                f"圓餅圖只能有一組系列，目前選了 {len(selected)} 組"
                f"（{list(selected)}）。請以 series_names 指定單一系列。"
            )

        if len(metric.categories) > MAX_PIE_CATEGORIES:
            errors.append(
                f"圓餅圖類別數 {len(metric.categories)} 超過可閱讀上限 "
                f"{MAX_PIE_CATEGORIES}，建議改用 bar 圖呈現排名。"
            )

        for series_name in selected:
            if series_name not in metric.series:
                continue

            negatives = [
                value
                for value in metric.series[series_name]
                if value is not None and value < 0
            ]

            if negatives:
                errors.append(
                    f"系列 {series_name!r} 含負值，無法以圓餅圖表達占比。"
                )

    if plan.chart_type == "scatter" and len(selected) != 2:
        errors.append(
            "散點圖需要恰好兩個系列（分別對應 x 軸與 y 軸），"
            f"目前選了 {len(selected)} 組（{list(selected)}）。"
        )

    if plan.chart_type == "combo" and len(selected) < 2:
        # 只有一個系列的「雙軸圖」沒有次軸可掛，等於一張普通長條圖，
        # 卻多帶了一組隱藏軸。這種圖應該一開始就用 column。
        errors.append(
            "雙軸圖需要至少兩個系列（第一個畫長條、其餘畫折線掛次軸），"
            f"目前選了 {len(selected)} 組（{list(selected)}）。"
            "單一系列請改用 column 或 line。"
        )

    if plan.chart_type in TABLE_LIKE_CHARTS:
        if len(metric.categories) > MAX_TABLE_ROWS:
            errors.append(
                f"表格列數 {len(metric.categories)} 超過單頁可閱讀上限 "
                f"{MAX_TABLE_ROWS}。請改用 bar 圖呈現排名，"
                "或選一個類別數較少的指標。"
            )

        if not any(
            value is not None
            for name in selected
            for value in metric.series.get(name, [])
        ):
            errors.append(
                f"指標 {plan.metric_key!r} 在系列 {list(selected)} 中"
                "沒有任何數值，表格會整片空白。"
            )

    return errors


# ---------------------------------------------------------------------------
# 查表組裝
# ---------------------------------------------------------------------------
def resolve_chart_plan(
    plan: ChartPlan,
    store: MetricStore,
    *,
    skip_validation: bool = False,
) -> ResolvedChart:
    """
    驗證通過後，從 MetricStore 查表取出實際數值組成 spec。

    **這是系統中唯一把數字填進圖表的地方**，數字全部來自 MetricStore，
    與 LLM 的輸出完全無關。

    Raises:
        ChartPlanError: 驗證未通過。
    """
    if not skip_validation:
        errors = validate_chart_plan(plan, store)

        if errors:
            raise ChartPlanError(
                f"ChartPlan 未通過檢查（metric_key={plan.metric_key!r}）："
                + "；".join(errors)
            )

    metric = store.get(plan.metric_key)
    selected = list(plan.series_names or metric.series_names)

    if plan.chart_type == "scatter":
        spec = _build_scatter_spec(plan, metric, selected)
    else:
        values = {
            name: _fill_missing(metric.values_for(name)) for name in selected
        }

        if plan.chart_type in TABLE_LIKE_CHARTS:
            spec = TableSpec(
                title=plan.chart_title or metric.name,
                categories=list(metric.categories),
                series=values,
                heatmap=plan.chart_type == "heatmap",
                row_header=_row_header_for(metric),
                unit=metric.unit,
                emphasize_rows=tuple(
                    label
                    for label in metric.categories
                    if is_total_category(label)
                ),
            )
        elif plan.chart_type == "combo":
            # 慣例：series_names 的第一個掛主軸畫長條，其餘畫折線掛次軸。
            # 由順序決定而不另開欄位，是為了讓 LLM 少一個可填錯的參數。
            spec = ComboSpec(
                title=plan.chart_title or metric.name,
                categories=list(metric.categories),
                series=values,
                chart_type=CHART_TYPE_BY_SKILL[plan.chart_type],
                line_series_names=tuple(selected[1:]),
            )
        else:
            spec = ChartSpec(
                title=plan.chart_title or metric.name,
                categories=list(metric.categories),
                series=values,
                chart_type=CHART_TYPE_BY_SKILL[plan.chart_type],
            )

    return ResolvedChart(
        plan=plan,
        skill_name=plan.chart_type,
        spec=spec,
        metric=metric,
        series_names=selected,
    )


def _row_header_for(metric: MetricSeries) -> str:
    """表格第一欄的表頭。時間軸的列是期間，橫斷面的列是實體。"""
    return "期間" if metric.axis_kind == AXIS_TEMPORAL else "項目"


def _build_scatter_spec(
    plan: ChartPlan,
    metric: MetricSeries,
    selected: Sequence[str],
) -> ScatterSpec:
    """
    兩個系列分別作為 x、y 軸，類別名稱作為資料點標籤。

    任一軸缺值的資料點整筆略過 —— 補 0 會在圖上產生不存在的座標點。
    """
    x_values = metric.values_for(selected[0])
    y_values = metric.values_for(selected[1])

    points: list[tuple[float, float]] = []
    labels: list[str] = []

    for index, category in enumerate(metric.categories):
        x = x_values[index]
        y = y_values[index]

        if x is None or y is None:
            continue

        points.append((x, y))
        labels.append(category)

    if not points:
        raise ChartPlanError(
            f"指標 {plan.metric_key!r} 的系列 {list(selected)} "
            "沒有任何同時具備 x、y 值的資料點，無法繪製散點圖。"
        )

    return ScatterSpec(
        title=plan.chart_title or metric.name,
        series_name=f"{selected[0]} vs {selected[1]}",
        points=points,
        labels=labels,
    )


def _fill_missing(values: Sequence[float | None]) -> list[float | None]:
    """
    缺值保持 None 交給 python-pptx（會呈現為空白資料點）。

    刻意不補 0：把缺值畫成 0 等於在圖上顯示一個不存在的觀測值，
    正是附件三「數字與 Excel 不符」問題的一種形式。
    """
    return list(values)


def resolve_all(
    plans: Sequence[ChartPlan],
    store: MetricStore,
) -> tuple[list[ResolvedChart], dict[int, list[str]]]:
    """
    批次驗證並查表。

    Returns:
        (成功的 ResolvedChart 清單, {plans 索引: 錯誤訊息清單})。
        失敗的項目不會中斷其他項目，由 Orchestrator 決定是退回 LLM
        重試該頁，還是整批放棄。
    """
    resolved: list[ResolvedChart] = []
    failures: dict[int, list[str]] = {}

    for index, plan in enumerate(plans):
        errors = validate_chart_plan(plan, store)

        if errors:
            failures[index] = errors
            continue

        try:
            resolved.append(resolve_chart_plan(plan, store, skip_validation=True))
        except ChartPlanError as error:
            failures[index] = [str(error)]

    return resolved, failures
