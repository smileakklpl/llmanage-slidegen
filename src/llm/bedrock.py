"""Bedrock adapter — 【尚未在真實環境驗證】

寫好放著，等拿到額度那天第一件事就是跑 evalh.harness --provider bedrock。
以下幾點是規格書預期、但必須實測確認的，驗過後把註記刪掉：

  [ ] Converse API 的 toolConfig 強制 schema 是否穩定（比 prompt 拜託可靠得多）
  [ ] model id 的 inference profile 前綴（us.）在 us-east-1 是否必要
  [ ] ThrottlingException 在 16-way 平行下的實際觸發點與錯誤形狀
  [ ] prompt caching 的實際命中率
  [ ] instance role 需要的 bedrock:InvokeModel 權限邊界（與部署負責人一起做）
"""

import json
import time
from typing import Type
from pydantic import BaseModel

from .base import LLMProvider, LLMResult
from .repair import complete_json_with_repair, default_repair_hint

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None

VERIFIED_ON_REAL_BEDROCK = False


class BedrockProvider(LLMProvider):
    name = "bedrock"

    def __init__(
        self,
        model: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        region: str = "us-east-1",
    ):
        self.model = model
        self.region = region
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def _converse(self, system: str, user: str, tool_config: dict | None = None) -> LLMResult:
        t0 = time.perf_counter()
        kwargs = {
            "modelId": self.model,
            "system": [{"text": system}],
            "messages": [{"role": "user", "content": [{"text": user}]}],
            "inferenceConfig": {"temperature": 0.2, "maxTokens": 4096},
        }
        if tool_config:
            kwargs["toolConfig"] = tool_config
        resp = self.client.converse(**kwargs)

        blocks = resp["output"]["message"]["content"]
        text = ""
        for b in blocks:
            if "toolUse" in b:
                text = json.dumps(b["toolUse"]["input"], ensure_ascii=False)
                break
            if "text" in b:
                text += b["text"]

        usage = resp.get("usage", {})
        return LLMResult(
            raw_text=text,
            provider=self.name,
            model=self.model,
            input_tokens=usage.get("inputTokens"),
            output_tokens=usage.get("outputTokens"),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    @staticmethod
    def _tool_config(schema: Type[BaseModel]) -> dict:
        """用 tool use 強制模型照 schema 吐，這是解掉 markdown fence 問題的正解。"""
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": "emit_result",
                        "description": "以指定結構回傳結果",
                        "inputSchema": {"json": schema.model_json_schema()},
                    }
                }
            ],
            "toolChoice": {"tool": {"name": "emit_result"}},
        }

    def complete_text(self, system: str, user: str, **kw) -> LLMResult:
        return self._converse(system, user)

    def complete_json(self, system, user, schema: Type[BaseModel], **kw) -> LLMResult:
        tc = self._tool_config(schema)
        return complete_json_with_repair(
            call=lambda hint: self._converse(system, user + hint, tool_config=tc),
            schema=schema,
            repair_hint_builder=default_repair_hint,
            fallback=kw.get("fallback"),
        )
