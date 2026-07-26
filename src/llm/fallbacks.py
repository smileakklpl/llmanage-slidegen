"""預設文案 fallback — 規格書 §6.2 的最後一段。

`repair.py` 早就支援 `fallback` 參數，兩個 adapter 也都會轉傳，
但在此之前沒有任何呼叫端傳入過，所以三次重試全敗時 `parsed` 是 None，
管線是**斷掉**而不是降級繼續。harness 報表上長期顯示的 `fallback 0%`
不是因為從沒觸發，是因為根本沒接。

## 什麼該有 fallback，什麼不該

有 fallback 的前提是「降級後的錯誤是**看得出來**的」。

  IntentSpec / PageNarrative —— 給。解析失敗時填一段明顯是佔位的文案，
    管線繼續走完，人一眼就看得出這頁沒寫好。

  SheetMap —— **不給，刻意的。** 結構定位錯了不會長得像錯的：
    total_row 填錯只會讓合計列被當成一般機構納入排名，
    產出一份數字全錯但外觀完全正常的簡報。
    這種情境寧可讓管線停下來喊失敗，也不要靜默降級。
    metric_definitions.json 已記載，合計列未排除會佔據第一名並使其後名次全部位移。

## 一條硬限制

PageNarrative 的文案**不得含任何阿拉伯數字**，否則 fallback 自己會被
contracts/narrative.py 的 validator 擋下來，變成「連降級都失敗」。
"""

import re
from typing import Optional, Type

from pydantic import BaseModel

from contracts.intent_spec import IntentSpec
from contracts.narrative import NarrativeBlock, PageNarrative

_NARRATIVE_ID = re.compile(r"n_p(\d+)")

FALLBACK_MARK = "【自動降級】"


def _intent_spec() -> IntentSpec:
    return IntentSpec(
        audience="銀行高階主管",
        page_count=16,
        sections=[
            "executive_summary",
            "market_overview",
            "competitive_landscape",
            "bank_deepdive",
        ],
        metrics=[f"{FALLBACK_MARK}意圖解析失敗，指標清單待人工補填"],
        assumptions=[
            f"{FALLBACK_MARK}模型三次輸出皆未通過 schema 驗證，"
            "本 IntentSpec 為預設值，所有欄位都需人工確認"
        ],
    )


def _page_narrative(source_text: str) -> PageNarrative:
    """頁碼與 narrative_id 從輸入原文取回，降級後才對得回原本那一頁。"""
    m = _NARRATIVE_ID.search(source_text or "")
    page = int(m.group(1)) if m else 1
    return PageNarrative(
        narrative_id=f"n_p{page}",
        page=page,
        title=f"{FALLBACK_MARK}本頁敘事待補",
        key_message=NarrativeBlock(
            text=f"{FALLBACK_MARK}敘事模組未能產出通過驗證的內容，"
            "本頁文案為預設值，須由人工補寫後才可對外使用。",
            claims=[],
        ),
        bullets=[],
    )


def build(schema: Type[BaseModel], source_text: str = "") -> Optional[BaseModel]:
    """回傳該 schema 的預設文案；不適合降級的 schema 回傳 None。"""
    if schema is IntentSpec:
        return _intent_spec()
    if schema is PageNarrative:
        return _page_narrative(source_text)
    return None
