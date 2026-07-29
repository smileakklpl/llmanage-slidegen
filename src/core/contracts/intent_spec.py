"""IntentSpec — 管線 [1] Intent Parser 的輸出契約。

對應規格書 §5.1。這是整條管線唯一輸入為自由文字的地方。
任何欄位變更都是跨模組破壞性變更，必須知會 engine 與 renderer 兩個模組的維護者。
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

SectionKey = Literal[
    "executive_summary",
    "market_overview",
    "competitive_landscape",
    "bank_deepdive",
    "trend_outlook",
    "appendix",
]

ChartPref = Literal[
    "ranking_bar",
    "line_bar_dual_axis",
    "scatter_size_vs_growth",
    "heatmap",
    "stacked_bar",
    "pie",
]


class IntentSpec(BaseModel):
    """使用者一句話 → 結構化簡報訂單。"""

    # 註：以下 description 不只是文件。約束解碼會把本 schema 直接餵進解碼器，
    # 此時欄位描述比 system prompt 更貼近生成點。實測 qwen2.5:14b：
    # 有 description 的欄位（page_count/audience/metrics）全部答對；
    # 無 description 的 chart_preferences 回空陣列、requested_derivations 自創中文 key。
    # 新增列舉型欄位時，務必連同「中文說法 → key」的對應一起寫進 description。

    audience: str = Field(description="簡報對象，例如「銀行高階主管」")
    page_count: int = Field(ge=1, le=60, description="目標頁數")
    sections: List[SectionKey] = Field(
        min_length=1,
        description="簡報章節。使用者的中文說法對應如下："
        "Executive Summary/重點摘要→executive_summary；"
        "市場整體概況/市場總覽→market_overview；"
        "同業競爭/競爭分析/市佔率排名→competitive_landscape；"
        "個別銀行分析/客戶活躍度/獲利能力→bank_deepdive；"
        "未來趨勢推測/展望/風險與警訊/策略建議→trend_outlook；"
        "附錄/附件/補充資料→appendix。"
        "使用者提到的每個主題都要對應，不得遺漏。"
        "**appendix 只在使用者明確要求附錄時才加入**——"
        "找不到對應的主題請歸入語意最接近的章節，不要用 appendix 收容。",
    )
    metrics: List[str] = Field(
        min_length=1,
        description="要呈現的指標語意名稱；非 MetricStore 的實際 key，由 engine 解析對應",
    )
    chart_preferences: List[ChartPref] = Field(
        default_factory=list,
        description="使用者要求的圖表型式。中文說法對應："
        "排名圖/長條排名/市占率圖→ranking_bar；"
        "成長率圖/雙軸圖/趨勢圖→line_bar_dual_axis；"
        "散點圖/規模 vs 成長→scatter_size_vs_growth；"
        "熱力圖→heatmap；堆疊圖→stacked_bar；圓餅圖→pie。"
        "使用者若列舉了圖表型式，本欄不得留空。",
    )
    recipients: List[str] = Field(
        default_factory=list,
        description="收件者的**電子郵件位址**，必須含 @。"
        "使用者只給稱謂而未給位址時（如「寄給老闆」「寄給行銷部」），"
        "本欄留空，並在 assumptions 記錄「使用者僅指定收件者為『老闆』，"
        "未提供位址，待 UI 補問」。**不得把稱謂當成位址填入**，會導致驗證失敗。",
    )
    style: Literal["consulting", "internal", "executive_brief"] = Field(
        default="consulting",
        description="敘事風格。顧問報告/McKinsey/BCG/策略簡報→consulting；"
        "內部檢討→internal；高層摘要→executive_brief。",
    )

    # 使用者明確要求、但資料可能不支援的推導指標（如 YoY）。
    # engine 依 FR-1.5 判定 computable；不可計算時必須拒絕產出，不得由 LLM 填補。
    requested_derivations: List[str] = Field(
        default_factory=list,
        description="使用者要求但需推導的指標，**必須使用下列受控字彙**，不得自創或用中文："
        "yoy_growth（年增率）、mom_growth（月增率/環比）、"
        "market_share_change（市占率變化）、cagr（複合成長率）。"
        "engine 依 FR-1.5 以這些 key 判定 computable，"
        "key 不一致會導致判定被跳過，等同放行未驗證的推導值。",
    )

    # 解析信心與缺漏欄位，供 UI 回問或走預設值
    assumptions: List[str] = Field(
        default_factory=list, description="使用者未指定而由系統採預設值的項目"
    )

    @field_validator("recipients")
    @classmethod
    def _basic_email_shape(cls, v: List[str]) -> List[str]:
        for addr in v:
            if "@" not in addr:
                raise ValueError(f"不是有效的收件者位址: {addr}")
        return v
