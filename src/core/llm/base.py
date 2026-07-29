"""LLM Provider 抽象層 — 規格書 FR-A1 / §4.3 `llm` 模組。

這是「要被評分的功能」，不是為了本地開發的權宜之計。
驗收條件：現場切換至地端開源模型仍可跑通，且數字不變。

刻意只定義三個方法。介面保持極窄，才換得動。
LiteLLM 若要用，是某個 adapter 的內部實作，不是這層介面本身。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel


def schema_doc(schema: Type[BaseModel]) -> str:
    """把 schema 的欄位描述渲染成文字，供塞進 prompt。

    約束解碼會丟掉 description，模型看不到——見 docs/設計決策.md。
    """
    s = schema.model_json_schema()
    defs = s.get("$defs", {})
    lines: List[str] = [f"# {schema.__name__} 各欄位說明"]

    def enum_of(node: Dict[str, Any]) -> Optional[List[str]]:
        if "enum" in node:
            return node["enum"]
        for key in ("items", "allOf", "anyOf"):
            sub = node.get(key)
            if isinstance(sub, dict):
                return enum_of(sub)
            if isinstance(sub, list):
                for x in sub:
                    got = enum_of(x) if isinstance(x, dict) else None
                    if got:
                        return got
        ref = node.get("$ref") or (node.get("items") or {}).get("$ref")
        if ref:
            return enum_of(defs.get(ref.rsplit("/", 1)[-1], {}))
        return None

    def render(props: Dict[str, Any], prefix: str = "") -> None:
        for name, node in props.items():
            desc = node.get("description", "")
            vals = enum_of(node)
            head = f"- {prefix}{name}"
            if vals:
                head += f"（可選值：{' | '.join(map(str, vals))}）"
            lines.append(f"{head}：{desc}" if desc else head)

    render(s.get("properties", {}))
    for dname, dnode in defs.items():
        if dnode.get("properties"):
            lines.append(f"\n## 巢狀物件 {dname}")
            render(dnode["properties"])
    return "\n".join(lines)


@dataclass
class LLMResult:
    """所有 provider 的統一回傳，供 eval harness 統計。"""

    raw_text: str
    parsed: Optional[Any] = None
    attempts: int = 1
    fell_back: bool = False
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    provider: str = ""
    model: str = ""
    errors: list = field(default_factory=list)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete_text(self, system: str, user: str, **kw) -> LLMResult:
        """自由文字補全。僅供除錯與 prompt 探索，管線不直接使用。"""

    @abstractmethod
    def complete_json(
        self,
        system: str,
        user: str,
        schema: Type[BaseModel],
        **kw,
    ) -> LLMResult:
        """結構化補全。管線唯一允許呼叫的方法。

        實作者責任：盡可能利用該 provider 的原生結構化輸出能力
        （Bedrock Converse toolConfig / Ollama format=json / OpenAI json_schema）。
        剝除 markdown fence、重試、fallback 一律交給 repair.py，
        adapter 不要各自實作一套。
        """

    def health(self) -> Dict[str, Any]:
        """開賽當天第一個跑的東西：確認這個 provider 現在活著。"""
        return {"provider": self.name, "ok": True}
