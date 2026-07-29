"""MockProvider — 今天就交給 A 和 C 的東西。

不呼叫任何模型，直接回 fixtures/golden/ 底下的固定 JSON。
用途有三：
  1. 讓 engine / renderer 立刻能對著真實形狀的資料開工，不必等 LLM 通
  2. 讓 CI 能在無網路、無金鑰的環境跑完整條管線
  3. 當作 eval harness 的對照組（mock 一定 100% 通過，通過率低於它就是模型問題）
"""

import json
import time
from pathlib import Path
from typing import Type
from pydantic import BaseModel

from .base import LLMProvider, LLMResult

from paths import GOLDEN


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, golden_dir: Path = GOLDEN, latency_ms: float = 0.0):
        self.golden_dir = Path(golden_dir)
        self.latency_ms = latency_ms

    def _load(self, schema: Type[BaseModel]) -> dict:
        # 以 schema 類別名對應檔名：IntentSpec -> intent_spec.json
        stem = "".join(
            "_" + c.lower() if c.isupper() else c for c in schema.__name__
        ).lstrip("_")
        path = self.golden_dir / f"{stem}.json"
        if not path.exists():
            raise FileNotFoundError(
                f"缺少 golden fixture: {path}。新增契約時要同步補一份範例。"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def complete_text(self, system: str, user: str, **kw) -> LLMResult:
        return LLMResult(raw_text="[mock] " + user[:80], provider=self.name)

    def complete_json(self, system, user, schema: Type[BaseModel], **kw) -> LLMResult:
        t0 = time.perf_counter()
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        data = self._load(schema)
        obj = schema.model_validate(data)
        return LLMResult(
            raw_text=json.dumps(data, ensure_ascii=False),
            parsed=obj,
            provider=self.name,
            model="golden-fixture",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
