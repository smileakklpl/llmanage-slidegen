"""Ollama adapter — 本地開發與「地端 LLM」demo 的主力。

FR-A1 的驗收條件是現場能換成開源模型跑通，所以這支不是備胎，是要上台的。
從第一天就跟 Bedrock 並行開發，抽象層才會真的被驗證過。
"""

import json
import time
from typing import Type
from pydantic import BaseModel

from .base import LLMProvider, LLMResult, schema_doc
from .repair import complete_json_with_repair, default_repair_hint

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


# llama.cpp 的 json-schema-to-grammar 不支援這些關鍵字，送過去會 400。
# PageNarrative 同時踩到三個：narrative_id 的 pattern、text 的 maxLength、
# bullets 的 maxItems——writer 因此整段跑不起來。
#
# 拿掉它們不等於放寬契約：grammar 只是解碼期的優化，真正的把關在
# repair.py 的 schema.model_validate()，違反長度或格式一樣會被擋下並重試。
# 差別只是「事前擋」變成「事後擋」，而 repair 管線本來就是為此存在的。
_GRAMMAR_UNSUPPORTED = (
    "pattern",
    "maxLength",
    "minLength",
    "maxItems",
    "minItems",
    "format",
)


def grammar_safe(node):
    """遞迴移除 llama.cpp grammar 不支援的 JSON Schema 關鍵字。"""
    if isinstance(node, dict):
        return {
            k: grammar_safe(v)
            for k, v in node.items()
            if k not in _GRAMMAR_UNSUPPORTED
        }
    if isinstance(node, list):
        return [grammar_safe(v) for v in node]
    return node


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        model: str = "qwen2.5:14b",
        host: str = "http://localhost:11434",
        num_ctx: int = 8192,
    ):
        self.model = model
        self.host = host.rstrip("/")
        # Ollama 預設 num_ctx 只有 2048，一份結構描述就可能超過。
        # 不顯式指定會被靜默截斷：模型看不到後半段工作表，卻仍吐出合法 JSON，
        # 計分器報「整張漏掉」，你會誤判成模型笨。
        #
        # 但也不要無腦開大。KV cache 隨 num_ctx 線性成長，16384 在 14B 上約佔 3 GB，
        # 會把本來放得進 8 GB VRAM 的層擠到 CPU（ollama ps 顯示 50%/50% CPU/GPU，
        # 單次呼叫慢到 20–117 秒）。實測各 stage 最大輸入約 3000 tokens、輸出約 600，
        # 8192 留了 2 倍餘裕仍足夠，截斷警告也還在守著。
        self.num_ctx = num_ctx

    def _chat(self, system: str, user: str, fmt: dict | None = None) -> LLMResult:
        t0 = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_ctx": self.num_ctx},
        }
        if fmt is not None:
            payload["format"] = fmt  # Ollama 支援直接傳 JSON Schema
        r = requests.post(f"{self.host}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        data = r.json()

        sent = data.get("prompt_eval_count")
        if sent and sent >= self.num_ctx - 8:
            print(
                f"⚠ 輸入疑似被截斷：prompt_eval_count={sent} "
                f"已貼近 num_ctx={self.num_ctx}"
            )

        return LLMResult(
            raw_text=data["message"]["content"],
            provider=self.name,
            model=self.model,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    def complete_text(self, system: str, user: str, **kw) -> LLMResult:
        return self._chat(system, user)

    def complete_json(self, system, user, schema: Type[BaseModel], **kw) -> LLMResult:
        fmt = grammar_safe(schema.model_json_schema())
        # grammar 保證「形狀合法」，schema_doc 補回「內容該填什麼」——
        # 後者在轉 grammar 時被丟掉了，不塞進 prompt 模型就看不到。
        #
        # 放 system 而非 user：欄位說明是規則，不是本次要處理的資料。
        # 混進 user message 會隨輸入一起被當成待解析內容，
        # 且 repair 重試時 hint 接在 user 尾端，說明會被推離指令區。
        system = f"{system}\n\n{schema_doc(schema)}"
        return complete_json_with_repair(
            call=lambda hint: self._chat(system, user + hint, fmt=fmt),
            schema=schema,
            repair_hint_builder=default_repair_hint,
            fallback=kw.get("fallback"),
        )

    def health(self):
        try:
            r = requests.get(f"{self.host}/api/tags", timeout=5)
            models = [m["name"] for m in r.json().get("models", [])]
            return {"provider": self.name, "ok": self.model in models, "models": models}
        except Exception as e:
            return {"provider": self.name, "ok": False, "error": str(e)}
