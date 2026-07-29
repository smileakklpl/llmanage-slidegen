"""
簡報生成模組 (ppt_generation)
==============================
消費 `src/backend` ingestion 管線輸出的 UnifiedIngestionResult JSON，
經確定性指標計算建立 MetricStore，再由多 Agent 決策圖表與敘事，
最終產出 PPT（原生圖表）與 FR-3 外部稽核 Excel。

核心原則（見 Guide.md 與 docs/圖表原生性與資料同步設計.md）：

1. LLM 只做決策，不計算、不產生任何數字。
2. 所有圖表一律經 ``charts.chart_builder`` 的 ``add_chart()`` 單一入口生成，
   確保 chart XML 快取值與內嵌工作簿天生一致。
3. 模組間僅以 JSON 契約溝通。

子套件對應管線階段：

| 子套件 | 階段 | 職責 |
|---|---|---|
| :mod:`data` | Stage 1-3 | backend JSON → 指標計算 → MetricStore |
| :mod:`charts` | — | 圖表定義、`add_chart()` 單一入口、ChartPlan 防呆 |
| :mod:`agents` | Stage 4 | 章節規劃 / 圖表決策 / 敘事撰寫 / 審查 |
| :mod:`output` | Stage 5-6 | .pptx 與稽核 .xlsx 產出 |
| :mod:`verification` | Stage 7 | 三方數值比對（T1） |
| :mod:`core` | 跨階段 | config／llm_client／placeholders |

套件根目錄只保留 :mod:`run_pipeline`（唯一 CLI 入口），
其餘實作一律歸屬子套件。
"""

from __future__ import annotations

__all__ = [
    "agents",
    "charts",
    "core",
    "data",
    "output",
    "verification",
]
