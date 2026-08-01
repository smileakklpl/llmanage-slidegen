"""
圖表 Agent
===========
對應 docs/圖表原生性與資料同步設計.md §4.2 ②。

本身是一個小型 Orchestrator，內部依序執行：

1. LLM 呼叫（tool use）→ 取得 skill 名稱與參數
2. 組裝 :class:`chart_planner.ChartPlan`（無數值欄位）
3. 確定性驗證 :func:`chart_planner.validate_chart_plan`
4. 確定性查表 :func:`chart_planner.resolve_chart_plan` → ChartSpec

驗證失敗時，把錯誤訊息回饋給 LLM 重試（錯誤訊息刻意寫成可讀的指引，
例如「line 圖只能用於時間序列指標，建議改用 column」），
讓模型自我修正而非直接放棄。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import llm_client
from ..charts.chart_builder import CHART_SKILL_TOOL_SCHEMAS
from ..charts.table_builder import TABLE_SKILL_TOOL_SCHEMAS
from ..charts.chart_planner import (
    ChartPlan,
    ChartPlanError,
    ResolvedChart,
    resolve_chart_plan,
    validate_chart_plan,
)
from ..data.metric_store import MetricStore
from .section_planner import SectionPlan, SectionPlanResult


logger = logging.getLogger(__name__)

#: 驗證失敗後允許 LLM 重試的次數。
MAX_PLAN_ATTEMPTS = 3

#: 交給 LLM 的完整 skill 清單（圖表 + 表格）。模型只會從這裡挑名稱，
#: 名稱之後還要過 chart_planner 的白名單，兩層都不放行未註冊的 skill。
VISUAL_SKILL_TOOL_SCHEMAS = [
    *CHART_SKILL_TOOL_SCHEMAS,
    *TABLE_SKILL_TOOL_SCHEMAS,
]

SYSTEM_PROMPT = """你是一位資料視覺化專家，為管理層簡報挑選圖表。

任務：為指定頁面挑選最合適的圖表類型、指標與系列，透過工具呼叫回傳。

嚴格規則：
1. 你只能填入 metric_key 與 series_names 引用指標，**絕對不可以填入任何實際數字**。
   工具參數中沒有數值欄位，這是刻意設計。
2. metric_key 與 series_names 必須來自本頁提供的指標目錄，不得自行發明，
   也不得選取目錄中未列於該 metric_key 下的其他系列。
3. series_names 不可省略或留空。只選與本頁標題及目的直接相關的系列；
   禁止因為同一 metric_key 還包含其他資料，就把所有系列一起放進圖表。
   一般單一主題圖選一個系列；只有目錄中的 comparison_reason 明確說明
   比較目的時才可選多個，而且只能選完成該比較所需的最少系列。
4. 圖表類型選擇原則：
   - axis_kind 為 temporal（時間序列）：用 line 呈現趨勢，或 column 比較期間
   - axis_kind 為 categorical（橫斷面分類）：用 column／bar 比較，
     或用 pie 呈現組成占比
   - line 圖不可用於 categorical 指標；pie 圖不可用於 temporal 指標
   - pie 圖只能有一組系列，且數值不可為負
   - scatter 圖需要恰好兩組系列（分別為 x 軸與 y 軸）
   - combo（雙軸圖）需要恰好兩組系列，適合兩組量級差距大但要並列看的系列；
     第一個系列畫長條掛主軸，第二個畫折線掛右側次軸
   - table（原生表格）適合要精確讀出每一格的明細；heatmap 適合
     多實體 × 多期間的強弱分佈。兩者列數上限 20，超過請改用 bar
5. chart_title 要有商業洞察意涵，不要只寫指標名稱。"""


@dataclass
class ChartAgentResult:
    """圖表 Agent 的輸出。"""

    charts: list[ResolvedChart] = field(default_factory=list)
    #: 未能產出圖表的章節標題 → 失敗原因
    failures: dict[str, list[str]] = field(default_factory=dict)
    #: 每個章節實際嘗試的次數，用於觀察模型穩定度
    attempts: dict[str, int] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return bool(self.charts) and not self.failures


def build_prompt(
    section: SectionPlan,
    store: MetricStore,
    previous_errors: list[str] | None = None,
    intent_spec: dict[str, Any] | None = None,
) -> str:
    """
    組裝 prompt。重試時附上上一輪的驗證錯誤，引導模型自我修正。
    """
    candidate_keys = list(section.suggested_metric_keys)
    candidate_set = set(candidate_keys)

    catalog = []
    for item in store.catalog_for_llm():
        if item["metric_key"] not in candidate_set:
            continue

        scoped = section.suggested_series_by_metric.get(
            item["metric_key"], []
        )
        narrowed = dict(item)
        narrowed["series_names"] = list(scoped)
        narrowed["series_units"] = {
            name: item.get("series_units", {}).get(name)
            for name in scoped
        }
        narrowed["comparison_reason"] = (
            section.comparison_reason_by_metric.get(item["metric_key"], "")
        )
        catalog.append(narrowed)

    parts = [
        "## 章節",
        f"標題：{section.title}",
        f"目的：{section.intent}",
        "",
        "## 本頁可用指標目錄（已依頁面主題限縮；僅 metadata，無實際數值）",
        json.dumps(catalog, ensure_ascii=False, indent=2),
        "",
        "只能使用上述 metric_key 與其列出的 series_names；不可擴大範圍。",
    ]

    if intent_spec:
        preferences = intent_spec.get("chart_preferences") or {}
        parts.extend(
            [
                "",
                "## 已驗證的呈現需求",
                f"簡報目的：{intent_spec.get('objective') or '依資料提供管理洞察'}",
                "偏好圖表："
                + json.dumps(
                    preferences.get("preferred_types") or [],
                    ensure_ascii=False,
                ),
                "避免圖表："
                + json.dumps(
                    preferences.get("avoided_types") or [],
                    ensure_ascii=False,
                ),
                "偏好只在符合 axis_kind、series 數量與原生物件規則時採用；"
                "不相容時請選擇合法圖表，不得拒絕產出。",
            ]
        )

    if previous_errors:
        parts.extend(
            [
                "",
                "## 上一次的嘗試未通過檢查，請修正後重新選擇",
                "\n".join(f"- {error}" for error in previous_errors),
            ]
        )

    return "\n".join(parts)


def plan_chart_for_section(
    section: SectionPlan,
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
    deadline_monotonic: float | None = None,
    intent_spec: dict[str, Any] | None = None,
) -> tuple[ResolvedChart | None, list[str], int]:
    """
    為單一章節產出一張已查表完成的圖表。

    Returns:
        (ResolvedChart 或 None, 最後一輪的錯誤訊息, 實際嘗試次數)。
    """
    call = llm_call or llm_client.complete_tool_call
    errors: list[str] = []

    if not section.suggested_metric_keys:
        return None, ["本頁沒有通過 section planner 核准的指標範圍"], 0

    for attempt in range(1, max_attempts + 1):
        tool_call = call(
            build_prompt(section, store, errors, intent_spec),
            VISUAL_SKILL_TOOL_SCHEMAS,
            system_prompt=SYSTEM_PROMPT,
            stage="chart",
            deadline_monotonic=deadline_monotonic,
        )

        plan = ChartPlan.from_tool_call(
            tool_call.name,
            tool_call.arguments,
            slide_title=section.title,
            page_number=section.page_number,
        )

        errors = validate_chart_plan(
            plan,
            store,
            allowed_metric_keys=section.suggested_metric_keys,
            allowed_series_by_metric=section.suggested_series_by_metric,
            comparison_reasons_by_metric=section.comparison_reason_by_metric,
        )

        if errors:
            logger.info(
                "章節 %r 第 %d 次圖表規劃未通過：%s",
                section.title,
                attempt,
                errors,
            )
            continue

        try:
            resolved = resolve_chart_plan(plan, store)
        except ChartPlanError as error:
            errors = [str(error)]
            continue

        return resolved, [], attempt

    return None, errors, max_attempts


def _deterministic_chart_type(
    axis_kind: str,
    intent_spec: dict[str, Any] | None,
) -> str:
    preferences = (intent_spec or {}).get("chart_preferences") or {}
    preferred = list(preferences.get("preferred_types") or [])
    avoided = set(preferences.get("avoided_types") or [])
    compatible = (
        {"line", "column", "bar", "table"}
        if axis_kind == "temporal"
        else {"column", "bar", "pie", "table"}
    )
    defaults = ["line", "column", "bar"] if axis_kind == "temporal" else [
        "bar",
        "column",
        "table",
    ]
    for candidate in [*preferred, *defaults]:
        if candidate in compatible and candidate not in avoided:
            return candidate

    remaining = sorted(compatible - avoided)
    if remaining:
        return remaining[0]

    if intent_spec is not None:
        warnings = intent_spec.setdefault("interpretation_warnings", [])
        warning = "所有相容圖表類型都被排除，為確保產出已忽略 avoided_types"
        if warning not in warnings:
            warnings.append(warning)
    return "line" if axis_kind == "temporal" else "bar"


def build_deterministic_chart(
    section: SectionPlan,
    store: MetricStore,
    intent_spec: dict[str, Any] | None = None,
) -> ResolvedChart:
    """Select a conservative native chart using validated section metadata."""
    errors_by_metric: list[str] = []

    for metric_key in section.suggested_metric_keys:
        metric = store.get(metric_key)
        scoped_series = section.suggested_series_by_metric.get(metric_key) or []
        series_names = list(scoped_series[:1] or metric.series_names[-1:])
        chart_type = _deterministic_chart_type(
            metric.axis_kind,
            intent_spec,
        )
        plan = ChartPlan(
            slide_title=section.title,
            chart_type=chart_type,
            chart_title=f"{metric.name}重點觀察",
            metric_key=metric_key,
            series_names=series_names,
            page_number=section.page_number,
        )
        errors = validate_chart_plan(
            plan,
            store,
            allowed_metric_keys=section.suggested_metric_keys,
            allowed_series_by_metric=section.suggested_series_by_metric,
            comparison_reasons_by_metric=section.comparison_reason_by_metric,
        )

        if errors:
            errors_by_metric.extend(errors)
            continue

        return resolve_chart_plan(plan, store)

    raise ChartPlanError(
        "無法從 section 白名單建立確定性圖表："
        + "；".join(errors_by_metric or ["沒有可用指標"])
    )


def plan_charts(
    sections: list[SectionPlan],
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
    deadline_monotonic: float | None = None,
    recover_provider_errors: bool = False,
    intent_spec: dict[str, Any] | None = None,
) -> ChartAgentResult:
    """
    為每個章節各產出一張圖表。

    單一章節失敗不會中斷其他章節 —— 缺一頁圖表仍可產出簡報，
    由 Orchestrator 決定是否要回報使用者或重試。
    """
    result = ChartAgentResult()

    for section in sections:
        try:
            resolved, errors, attempts = plan_chart_for_section(
                section,
                store,
                llm_call=llm_call,
                max_attempts=max_attempts,
                deadline_monotonic=deadline_monotonic,
                intent_spec=intent_spec,
            )
        except Exception as error:  # noqa: BLE001 - policy decides recovery
            if not recover_provider_errors:
                raise
            resolved = None
            errors = [f"{type(error).__name__}: {error}"]
            attempts = 0

        result.attempts[section.title] = attempts

        if resolved is None:
            result.failures[section.title] = errors or ["未知原因"]
            continue

        result.charts.append(resolved)

    return result


def plan_charts_from_contract(
    section_stage_payload: dict[str, Any],
    metric_store_payload: dict[str, Any],
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
    deadline_monotonic: float | None = None,
    recover_provider_errors: bool = False,
) -> dict[str, Any]:
    """JSON-only stage boundary for chart planning; output contains no values."""
    from ..contracts import stages as stage_contracts

    sections_json = stage_contracts.section_stage_payload(
        section_stage_payload
    )
    sections = SectionPlanResult.from_dict(sections_json).sections
    intent_spec = dict(sections_json.get("intent_spec") or {})
    store_json = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(store_json)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    result = plan_charts(
        sections,
        store,
        llm_call=llm_call,
        max_attempts=max_attempts,
        deadline_monotonic=deadline_monotonic,
        recover_provider_errors=recover_provider_errors,
        intent_spec=intent_spec,
    )
    return stage_contracts.chart_stage_payload(
        {
            "plans": [chart.plan.to_dict() for chart in result.charts],
            "failures": result.failures,
            "attempts": result.attempts,
        }
    )


def build_deterministic_chart_from_contract(
    section_payload: dict[str, Any],
    metric_store_payload: dict[str, Any],
    *,
    intent_spec_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """JSON-only deterministic chart fallback returning a value-free plan."""
    from ..contracts import stages as stage_contracts

    section_json = stage_contracts.SectionContract.model_validate(
        section_payload
    ).model_dump(mode="json")
    section = SectionPlan.from_dict(section_json)
    store_json = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(store_json)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    plan = build_deterministic_chart(
        section,
        store,
        intent_spec_payload,
    ).plan
    return stage_contracts.ChartPlanContract.model_validate(
        plan.to_dict()
    ).model_dump(mode="json")