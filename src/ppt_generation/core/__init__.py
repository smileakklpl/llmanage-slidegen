"""
跨階段共用基礎設施 (core)
==========================
不屬於任何單一管線階段，被多個子套件共用的三個模組。

| 模組 | 職責 | 被誰用 |
|---|---|---|
| :mod:`config` | 路徑常數、憑證載入、`LLMSettings`（模型能力自動判斷） | 全部 |
| :mod:`llm_client` | `complete_json()` / `complete_tool_call()`；模型可抽換層 | `agents` |
| :mod:`placeholders` | 敘事佔位符解析、查表代入、裸數字偵測 | `agents`、`output` |
| :mod:`theme` | 顏色、字體、幾何常數與自適應字級；唯一視覺來源 | `charts`、`output` |

:mod:`theme` 原本在 ``output/``，但 :mod:`charts` 也要用它上色。留在
``output/`` 會讓 ``charts`` 的匯入繞經 ``output/__init__`` → ``renderer``
→ ``charts``，形成循環匯入，因此移到這裡。

放在這裡而非套件根目錄，是為了讓 `ppt_generation/` 根層只留 `run_pipeline.py`
這個唯一入口，其餘一律歸屬子套件。

注意 :mod:`config` 的 ``PROJECT_ROOT`` 由本檔案位置往上推算，
若再次搬移目錄層級，需同步修正 ``parents[n]``。
"""

from __future__ import annotations

__all__ = [
    "config",
    "llm_client",
    "placeholders",
    "theme",
]
