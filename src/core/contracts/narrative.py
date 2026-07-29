"""PageNarrative — 管線 [3] Insight Writer 的輸出契約。

對應規格書 §4.3 `writer` 模組、§6.1（LLM 永不產生數字）、T8（敘事一致性）。

兩條硬規則寫進 schema，不是寫進文件：
  1. 任何 text 欄位不得出現實際數值，只能用 {{metric_key}} 佔位符。
  2. 比較句必須同時輸出機器可驗證的 Claim，供 validator 對 MetricStore 斷言。
"""

import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

PLACEHOLDER = re.compile(r"\{\{([a-zA-Z0-9_:\.]+)\}\}")

# 硬失敗：阿拉伯數字（含全形）出現在佔位符之外
_ARABIC = re.compile(r"[0-9０-９]")

# 軟警告：中文數字接數量單位，常見於「成長三成」「約五億」這類漏網之魚
_CJK_NUM_UNIT = re.compile(
    r"[一二三四五六七八九十百千兩零壹貳參肆伍陸柒捌玖拾]\s*"
    r"(?:成|倍|億|萬|千萬|百萬|個百分點|%|％|percent)"
)


def strip_placeholders(text: str) -> str:
    return PLACEHOLDER.sub("", text)


def find_numeric_violations(text: str) -> List[str]:
    """回傳違規片段列表；空 list 代表通過。"""
    bare = strip_placeholders(text)
    hits: List[str] = []
    for m in _ARABIC.finditer(bare):
        s = max(0, m.start() - 6)
        hits.append(f"阿拉伯數字: …{bare[s:m.end() + 6]}…")
    for m in _CJK_NUM_UNIT.finditer(bare):
        hits.append(f"中文數量詞: {m.group(0)}")
    return hits


class Claim(BaseModel):
    """比較句的機器可驗證宣告（T8）。

    例：敘事寫「A 銀行市占 {{m_a_share}} 高於 B 銀行 {{m_b_share}}」
        則 claim = {op: "gt", left: "m_a_share", right: "m_b_share"}
    validator 代入 MetricStore 實值後斷言此關係成立。
    """

    op: Literal["gt", "lt", "eq", "gte", "lte", "rank"]
    left: str = Field(description="MetricStore key")
    right: str = Field(description="MetricStore key，或 rank 時為名次整數字串")


class NarrativeBlock(BaseModel):
    text: str = Field(max_length=400)
    claims: List[Claim] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def _no_literal_numbers(cls, v: str) -> str:
        violations = find_numeric_violations(v)
        if violations:
            raise ValueError(
                "敘事不得含實際數值，請改用 {{metric_key}} 佔位符。違規："
                + "; ".join(violations[:3])
            )
        return v

    @property
    def referenced_metrics(self) -> List[str]:
        return PLACEHOLDER.findall(self.text)


class PageNarrative(BaseModel):
    """單頁敘事。renderer 以 narrative_id 從 DeckSpec 的 narrative_ref 取用。"""

    narrative_id: str = Field(pattern=r"^n_p\d+$", description="例：n_p5")
    page: int = Field(ge=1)
    title: str = Field(max_length=40)
    key_message: NarrativeBlock = Field(description="頁面主標下的一句重點")
    bullets: List[NarrativeBlock] = Field(default_factory=list, max_length=5)
    chart_caption: Optional[NarrativeBlock] = None

    @field_validator("title")
    @classmethod
    def _title_no_numbers(cls, v: str) -> str:
        if find_numeric_violations(v):
            raise ValueError("標題不得含實際數值")
        return v

    def all_referenced_metrics(self) -> List[str]:
        keys: List[str] = list(self.key_message.referenced_metrics)
        for b in self.bullets:
            keys.extend(b.referenced_metrics)
        if self.chart_caption:
            keys.extend(self.chart_caption.referenced_metrics)
        return sorted(set(keys))

    def all_claims(self) -> List[Claim]:
        out = list(self.key_message.claims)
        for b in self.bullets:
            out.extend(b.claims)
        if self.chart_caption:
            out.extend(self.chart_caption.claims)
        return out


class NarrativeSet(BaseModel):
    pages: List[PageNarrative]
