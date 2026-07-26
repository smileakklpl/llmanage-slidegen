"""SheetMap — 管線 [1.5] 結構定位的輸出契約。

規格書 §5.2 的 MetricStore 每個指標都帶 source(sheet/range/formula)，
但沒說那個對應是誰產生的。SheetMap 就是那個缺口：
它描述「這張工作表長什麼樣」，讓 pandas 不必為每張表寫一支程式。

設計原則：描述**形狀參數**，不是描述某一張特定的表。
附件四的四張工作表只差三個參數（總計列位置、有無衍生欄、列排序），
因此同一支 reader 加上不同的 SheetMap 就能全部吃下。
"""

from typing import List, Literal, Optional
from pydantic import BaseModel, Field

Archetype = Literal[
    "entity_by_period",   # 實體 × 期間 寬表（附件四四張皆是）
    "entity_by_metric",   # 實體 × 多指標
    "long_records",       # 長格式逐筆記錄
    "unknown",
]

RowOrder = Literal["source", "sorted_desc", "sorted_asc", "unknown"]


class ColumnSpec(BaseModel):
    header: str = Field(description="標題列上的原始文字，逐字照抄")
    col_letter: str = Field(pattern=r"^[A-Z]{1,2}$")
    role: Literal["entity", "period", "derived", "ignore"]
    period_key: Optional[str] = Field(
        default=None, description="role=period 時的正規化期間，如 '11412'"
    )


class SheetSpec(BaseModel):
    sheet_name: str
    archetype: Archetype

    # 一份 SheetMap 可以橫跨多個檔案：附件四是「1 檔 × 4 表」，
    # 一指標一檔的資料集是「N 檔 × 1 表」，兩者塌陷成同一組 (檔案, 工作表) 配對。
    # None 表示沿用 SheetMap.workbook。模型不必填這欄，由呼叫端補。
    source_file: Optional[str] = Field(
        default=None, exclude=True, description="這張表來自哪個檔案；None 表示 SheetMap.workbook"
    )

    header_row: int = Field(ge=1, description="標題列的列號（1-based）")
    # 註：description 內**不要寫任何具體列號**。同一份描述會對每張工作表顯示，
    # 模型會把舉例的數字當成答案抄過去——實測寫了「P.5 合計列在第 34 列」之後，
    # P.7 兩張表的 total_row 全被填成 34（正確為 2）。一律改用程序性指示。
    first_data_row: int = Field(
        ge=2,
        description="資料區的第一列（1-based），通常緊接在 header_row 之後。"
        "**合計列包含在資料區內**——它由 total_row 另行標示、由 reader 負責排除，"
        "不是靠縮小資料範圍來排除。即使合計列就在資料區首列，本欄仍填該列列號。",
    )
    last_data_row: int = Field(
        ge=2,
        description="資料區的最後一列（1-based），同樣包含合計列。"
        "以結構描述中「實際資料範圍」的列數為準。",
    )

    columns: List[ColumnSpec]

    total_row: Optional[int] = Field(
        default=None,
        description="合計列的列號（1-based）。找法：在結構描述的「前 N 列」與「後 N 列」"
        "樣本中，找出實體欄文字為「總計」「合計」「小計」「Total」的那一列，"
        "**直接讀取該列左側已標明的列號**，不要推算、不要沿用其他工作表的列號。"
        "合計列在首列或末列都很常見，兩種都要找。"
        "找不到才填 null；漏填的後果是合計列被當成一般機構納入排名，"
        "會佔據第一名並使其後所有名次位移。",
    )
    total_label: Optional[str] = Field(default=None, description="合計列的標籤文字，如「總計」")

    # description 不是文件，是給模型的指令。約束解碼（Ollama format / Bedrock toolConfig）
    # 會把 schema 直接餵進解碼器，此時欄位描述比 system prompt 更貼近生成點。
    # 實測：本欄無描述且帶 default 時，qwen2.5:14b 四張表全部吐 "unknown"（0/4）；
    # 同一次執行中，有描述的 total_row / sorted_by 則都有嘗試作答。
    row_order: RowOrder = Field(
        default="unknown",
        description="資料列的排序狀態。**直接依結構描述中的「數值走勢」段落判定，"
        "不要自己比對樣本數值**——該段落是程式算出的算術事實。"
        "任一欄顯示 desc（三種切法之一即可）→ sorted_desc，並把該欄標題填入 sorted_by；"
        "任一欄顯示 asc → sorted_asc；"
        "顯示「所有數值欄皆非單調」→ source（未經排序，維持來源檔原始順序，很常見）；"
        "unknown 僅在沒有數值走勢資訊時使用。"
        "注意合計列的位置與排序無關，不可用它推論排序。",
    )
    sorted_by: Optional[str] = Field(default=None, description="排序依據的欄位標題")

    notes: List[str] = Field(
        default_factory=list, description="定位過程中發現的異常，供人工複核"
    )

    # --- 便利存取 ---
    @property
    def entity_col(self) -> Optional[str]:
        return next((c.col_letter for c in self.columns if c.role == "entity"), None)

    @property
    def period_cols(self) -> List[str]:
        return [c.col_letter for c in self.columns if c.role == "period"]

    @property
    def derived_cols(self) -> List[str]:
        return [c.col_letter for c in self.columns if c.role == "derived"]


class SheetMap(BaseModel):
    """整份活頁簿的結構描述。"""

    workbook: str
    sheets: List[SheetSpec]

    def get(self, name: str) -> Optional[SheetSpec]:
        return next((s for s in self.sheets if s.sheet_name == name), None)
