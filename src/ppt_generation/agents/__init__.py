"""
多 Agent 協作層
================
對應 docs/圖表原生性與資料同步設計.md §4。

四個 Agent 各自職責單一，彼此僅以 JSON 契約溝通：

| Agent | 模組 | 輸入 → 輸出 |
|---|---|---|
| 章節規劃 | :mod:`section_planner` | user_prompt → SectionPlan[] |
| 圖表決策 | :mod:`chart_agent` | SectionPlan + catalog → ChartPlan → ResolvedChart |
| 敘事撰寫 | :mod:`narrative_writer` | SectionPlan + ResolvedChart → PageNarrative |
| 審查 | :mod:`reviewer` | PageNarrative + ResolvedChart → APPROVED / REJECTED |

共同設計約束：
1. 每個 Agent 的 LLM 呼叫都可注入（``llm_call`` 參數），便於測試時
   以假回應替換，不需連網。
2. LLM 輸出一律先經確定性驗證，失敗時把錯誤訊息回饋給 LLM 重試，
   重試用盡則回報失敗，不讓半成品流入下游。
3. 沒有任何 Agent 能產生數字 —— 圖表數值由 MetricStore 查表，
   敘事數值由佔位符在 renderer 階段代入。
"""

from __future__ import annotations

__all__ = [
    "chart_agent",
    "narrative_writer",
    "reviewer",
    "section_planner",
]
