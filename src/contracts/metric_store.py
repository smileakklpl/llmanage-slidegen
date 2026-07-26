"""MetricStore — 管線 [2] Data Engine 的輸出契約。

對應規格書 §5.2。這是整個系統唯一的數值真相來源：
writer 只能引用它的 key、renderer 只能代入它的 value、validator 只能對它斷言。

三個設計重點：

1. **每個指標都帶 source。** 規格書要求 `{sheet, range, formula}`，
   目的是任何一個數字都能被追回到來源儲存格。做不到追溯的數字，
   就跟 LLM 幻覺沒有區別——差別只在它是程式編的。

2. **不可計算是一等公民，不是例外。** `computable=false` 帶 `reason`，
   而不是丟出例外或填 0。附件三錯誤 #1（無 113 基期卻產出 YoY）
   就是把「算不出來」當成「算出來是 0」的後果。

3. **value 允許 None。** computable=false 時沒有值可填，
   強迫填一個佔位數字只會讓下游誤用。
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class MetricSource(BaseModel):
    """數值的來源追溯。任何一個 value 都要能循此找回原始儲存格。"""

    sheet: str
    range: str = Field(description="A1 表示法，如 'B34:M34'")
    formula: str = Field(description="人可讀的計算式，如 'SUM(all_periods)/SUM(total)'")


class Metric(BaseModel):
    key: str
    value: Optional[float] = None
    unit: str = ""
    source: Optional[MetricSource] = None
    computable: bool = True
    reason: Optional[str] = Field(
        default=None, description="computable=false 時必填，說明為何算不出來"
    )
    label: str = Field(default="", description="人可讀名稱，供人工複核用，不進敘事")


class MetricStore(BaseModel):
    metrics: Dict[str, Metric] = Field(default_factory=dict)

    def add(self, m: Metric) -> None:
        self.metrics[m.key] = m

    def get(self, key: str) -> Optional[Metric]:
        return self.metrics.get(key)

    def value_of(self, key: str) -> Optional[float]:
        m = self.metrics.get(key)
        return m.value if m and m.computable else None

    def computable_keys(self) -> List[str]:
        return sorted(k for k, m in self.metrics.items() if m.computable)

    def uncomputable_keys(self) -> List[str]:
        return sorted(k for k, m in self.metrics.items() if not m.computable)
