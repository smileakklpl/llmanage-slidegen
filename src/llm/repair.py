"""輸出修復管線 — 規格書 §6.2（schema 驗證 + 重試上限 2 次 + 預設文案 fallback）。

四段全部可單獨測試，不要混在 adapter 裡。
工作坊已量到 Haiku 會穩定用 markdown fence 包 JSON，所以第 1 段從第一天就要有。
"""

import json
import re
from typing import Any, Callable, Optional, Tuple, Type
from pydantic import BaseModel, ValidationError

from llm.base import LLMResult

_FENCE = re.compile(r"^\s*```(?:json|JSON)?\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)
_FIRST_OBJ = re.compile(r"(\{.*\}|\[.*\])", re.DOTALL)


# --- 段 1：剝除 markdown fence -------------------------------------------
def strip_fence(text: str) -> str:
    m = _FENCE.match(text)
    if m:
        return m.group(1)
    return text


# --- 段 2：容錯抽取與修補 -------------------------------------------------
def extract_json(text: str) -> str:
    """模型有時會在 JSON 前後加一句「好的，以下是…」，抓出最外層物件。"""
    t = strip_fence(text).strip()
    if t.startswith("{") or t.startswith("["):
        return t
    m = _FIRST_OBJ.search(t)
    return m.group(1) if m else t


def loads_lenient(text: str) -> Any:
    t = extract_json(text)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 常見小毛病：尾逗號、全形引號
        t2 = re.sub(r",(\s*[}\]])", r"\1", t)
        t2 = t2.replace("“", '"').replace("”", '"').replace("，", ",")
        return json.loads(t2)


# --- 段 3+4：驗證、重試、fallback ----------------------------------------
def complete_json_with_repair(
    call: Callable[[str], LLMResult],
    schema: Type[BaseModel],
    repair_hint_builder: Callable[[str], str],
    max_retries: int = 2,
    fallback: Optional[BaseModel] = None,
) -> LLMResult:
    """
    call: 接收「附加提示（可為空字串）」並實際打模型的函式
    repair_hint_builder: 由錯誤訊息生成給模型的修正提示
    """
    errors: list = []
    last: Optional[LLMResult] = None
    hint = ""

    for attempt in range(1, max_retries + 2):
        res = call(hint)
        last = res
        res.attempts = attempt
        try:
            data = loads_lenient(res.raw_text)
            res.parsed = schema.model_validate(data)
            res.errors = errors
            return res
        except (json.JSONDecodeError, ValidationError) as e:
            msg = f"attempt {attempt}: {type(e).__name__}: {e}"
            errors.append(msg)
            hint = repair_hint_builder(str(e))

    # 全部失敗 → 預設文案，絕不讓管線中斷（但要標記，validator 會在佐證附錄註明）
    out = last or LLMResult(raw_text="")
    out.errors = errors
    out.attempts = max_retries + 1
    if fallback is not None:
        out.parsed = fallback
        out.fell_back = True
    return out


def default_repair_hint(err: str) -> str:
    return (
        "\n\n[系統修正提示] 你上一次的輸出無法通過 schema 驗證，錯誤如下：\n"
        f"{err[:600]}\n"
        "請只輸出符合 schema 的 JSON 物件本身，不要加任何說明文字，"
        "不要使用 markdown 程式碼區塊，敘事欄位中的所有數值一律改為 "
        "{{metric_key}} 佔位符。"
    )
