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
from ..charts.chart_planner import (
    ChartPlan,
    ChartPlanError,
    ResolvedChart,
    resolve_chart_plan,
    validate_chart_plan,
)
from ..data.metric_store import MetricStore
from .section_planner import SectionPlan


logger = logging.getLogger(__name__)

#: 驗證失敗後允許 LLM 重試的次數。
MAX_PLAN_ATTEMPTS = 3

SYSTEM_PROMPT = """你是一位資料視覺化專家，為銀行高階主管簡報挑選圖表。

任務：為指定章節挑選最合適的圖表類型與指標，透過工具呼叫回傳。

嚴格規則：
1. 你只能填入 metric_key 引用指標，**絕對不可以填入任何實際數字**。
   工具參數中沒有數值欄位，這是刻意設計。
2. metric_key 必須來自提供的指標目錄，不得自行發明。
3. 圖表類型選擇原則：
   - axis_kind 為 temporal（時間序列）：用 line 呈現趨勢，或 column 比較期間
   - axis_kind 為 categorical（橫斷面分類）：用 column／bar 比較，
     或用 pie 呈現組成占比
   - line 圖不可用於 categorical 指標；pie 圖不可用於 temporal 指標
   - pie 圖只能有一組系列，且數值不可為負
   - scatter 圖需要恰好兩組系列（分別為 x 軸與 y 軸）
4. chart_title 要有商業洞察意涵，不要只寫指標名稱。"""


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
) -> str:
    """
    組裝 prompt。重試時附上上一輪的驗證錯誤，引導模型自我修正。
    """
    candidate_keys = section.suggested_metric_keys or store.computable_metric_keys()

    catalog = [
        item
        for item in store.catalog_for_llm()
        if item["metric_key"] in set(candidate_keys)
    ]

    # 章節建議的指標若全被剔除，退回全部可用指標，避免整頁生不出來。
    if not catalog:
        catalog = store.catalog_for_llm()

    parts = [
        "## 章節",
        f"標題：{section.title}",
        f"目的：{section.intent}",
        "",
        "## 可用指標目錄（僅 metadata，無實際數值）",
        json.dumps(catalog, ensure_ascii=False, indent=2),
    ]

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
) -> tuple[ResolvedChart | None, list[str], int]:
    """
    為單一章節產出一張已查表完成的圖表。

    Returns:
        (ResolvedChart 或 None, 最後一輪的錯誤訊息, 實際嘗試次數)。
    """
    call = llm_call or llm_client.complete_tool_call
    errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        tool_call = call(
            build_prompt(section, store, errors),
            CHART_SKILL_TOOL_SCHEMAS,
            system_prompt=SYSTEM_PROMPT,
            stage="chart",
        )

        plan = ChartPlan.from_tool_call(
            tool_call.name,
            tool_call.arguments,
            slide_title=section.title,
            page_number=section.page_number,
        )

        errors = validate_chart_plan(plan, store)

        if errors:
            logger.info(
                "章節 %r 第 %d 次圖表規劃未通過：%s",
                section.title,
                attempt,
                errors,
            )
            continue

        try:
            resolved = resolve_chart_plan(plan, store, skip_validation=True)
        except ChartPlanError as error:
            errors = [str(error)]
            continue

        return resolved, [], attempt

    return None, errors, max_attempts


def plan_charts(
    sections: list[SectionPlan],
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_PLAN_ATTEMPTS,
) -> ChartAgentResult:
    """
    為每個章節各產出一張圖表。

    單一章節失敗不會中斷其他章節 —— 缺一頁圖表仍可產出簡報，
    由 Orchestrator 決定是否要回報使用者或重試。
    """
    result = ChartAgentResult()

    for section in sections:
        resolved, errors, attempts = plan_chart_for_section(
            section,
            store,
            llm_call=llm_call,
            max_attempts=max_attempts,
        )

        result.attempts[section.title] = attempts

        if resolved is None:
            result.failures[section.title] = errors or ["未知原因"]
            continue

        result.charts.append(resolved)

    return result
