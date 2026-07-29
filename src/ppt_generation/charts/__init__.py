"""
圖表定義與防呆
===============
- :mod:`chart_builder` ── `ChartSpec` / `ScatterSpec` 與 ``add_chart()`` 單一入口；
  圖表 skill registry 與給 LLM 的 tool schema
- :mod:`chart_planner` ── `ChartPlan`（LLM 唯一允許的輸出格式，無數值欄位）
  的驗證與查表組裝

**所有圖表都必須經 chart_builder 的 add_chart() 生成**，這樣 chart XML 快取與
內嵌工作簿才會天生一致（見 docs/圖表原生性與資料同步設計.md §2.2）。
"""

from __future__ import annotations

from .chart_builder import (
    CHART_SKILL_TOOL_SCHEMAS,
    CHART_SKILLS,
    CHART_TYPE_BY_SKILL,
    ChartSpec,
    ScatterSpec,
    add_category_chart,
    add_pie_chart,
    add_scatter_chart,
    dispatch_chart_skill,
)
from .chart_planner import (
    ChartPlan,
    ChartPlanError,
    ResolvedChart,
    resolve_all,
    resolve_chart_plan,
    validate_chart_plan,
)

__all__ = [
    "CHART_SKILLS",
    "CHART_SKILL_TOOL_SCHEMAS",
    "CHART_TYPE_BY_SKILL",
    "ChartPlan",
    "ChartPlanError",
    "ChartSpec",
    "ResolvedChart",
    "ScatterSpec",
    "add_category_chart",
    "add_pie_chart",
    "add_scatter_chart",
    "dispatch_chart_skill",
    "resolve_all",
    "resolve_chart_plan",
    "validate_chart_plan",
]
