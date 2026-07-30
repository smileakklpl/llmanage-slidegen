"""
原生表格與熱力圖
=================
對應 FR-2.4（表格一律為 PPT 原生 table 物件，禁止文字方塊拼貼）與
FR-2.3 的熱力圖條目（無原生熱力圖類型，以原生表格 + 儲存格底色模擬）。

與 :mod:`chart_builder` 的分工
-------------------------------
圖表走 ``shapes.add_chart()``，有 chart XML 快取與內嵌 workbook 兩份副本；
表格走 ``shapes.add_table()``，**只有儲存格文字一份**——沒有內嵌 workbook，
右鍵也沒有「編輯資料」。這不是缺陷，是 PowerPoint 表格本來的樣子。

但它有個直接後果：表格數字無法靠「①②天生一致」來保證正確。因此本模組
規定表格文字一律由 :func:`format_value` 這一個函式產生，並讓
:mod:`verification.verify_chart_consistency` 反向解析儲存格文字與稽核
Excel 比對（見該模組的表格比對段）。表格的數字正確性靠這條驗證守著。

熱力圖的色階
-------------
色階只是視覺編碼，不得改變任何數值。分母取該表所有數值的極值，
線性插值到兩個端點色之間；缺值不上色（留白），不塗成最小值的顏色——
把缺值畫成「最冷的格子」等於宣稱它是最小值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from pptx.dml.color import RGBColor
from pptx.slide import Slide
from pptx.util import Emu, Pt

from .chart_builder import ChartSpec


#: 表頭底色與文字色（對齊模板主色）。
HEADER_FILL = RGBColor(0x1F, 0x38, 0x64)
HEADER_FONT_COLOR = RGBColor(0xFF, 0xFF, 0xFF)

#: 熱力圖色階兩端。低值為淺、高值為深，保留足夠對比讓深底上的字仍可讀。
HEATMAP_LOW = RGBColor(0xE8, 0xF1, 0xFA)
HEATMAP_HIGH = RGBColor(0x1F, 0x38, 0x64)

#: 底色亮度低於此門檻時，文字改為白色以維持可讀性（無障礙要求）。
_DARK_BACKGROUND_THRESHOLD = 0.55

#: 表格字級。
TITLE_FONT_SIZE = Pt(14)
HEADER_FONT_SIZE = Pt(11)
BODY_FONT_SIZE = Pt(11)

#: 一頁能放得下的資料列上限。超過就不是給人看的表了，應改用圖表或先取 Top N。
MAX_TABLE_ROWS = 20


@dataclass
class TableSpec(ChartSpec):
    """
    原生表格規格。

    刻意繼承 :class:`ChartSpec`：``categories`` 為列標籤、``series`` 為欄，
    形狀與圖表完全相同，稽核 Excel 匯出與 planner 查表都不必分支。
    ``chart_type`` 欄位對表格無意義，不使用。
    """

    #: True 時依數值大小為儲存格上色（熱力圖）。
    heatmap: bool = False
    #: 列標籤欄的表頭文字。
    row_header: str = "項目"
    #: 數值單位，用於格式化與表頭標注。
    unit: str | None = None
    #: 需要以粗體強調的列標籤（例如合計列）。
    emphasize_rows: tuple[str, ...] = field(default_factory=tuple)


class TableBuildError(ValueError):
    """表格規格不可用。"""


# ---------------------------------------------------------------------------
# 數值格式化（唯一入口）
# ---------------------------------------------------------------------------
def format_value(value: float | None, unit: str | None = None) -> str:
    """
    把數值格式化成儲存格文字。

    **表格文字一律經由此函式產生**，因為驗證要反向解析它。任何地方
    自己 f-string 出一個數字，就等於在系統裡開了第二個數字來源。

    缺值輸出 ``"—"``（破折號）而非 ``0`` 或空字串：0 是一個觀測值，
    空字串看起來像漏排版，破折號才明確表示「此格無資料」。
    """
    if value is None:
        return "—"

    if unit in {"%", "％"}:
        return f"{value:.2f}%"

    if unit == "名":
        return f"{int(round(value))}"

    if float(value).is_integer() and abs(value) < 1e15:
        return f"{int(value):,}"

    return f"{value:,.2f}"


def parse_value(text: str, unit: str | None = None) -> float | None:
    """
    :func:`format_value` 的反向操作，供驗證比對使用。

    解析不出來時回傳 None，由呼叫端決定要不要視為失敗——這裡不擅自
    當成 0。
    """
    cleaned = str(text).strip().replace(",", "").replace("％", "").rstrip("%")

    if not cleaned or cleaned == "—":
        return None

    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 色階
# ---------------------------------------------------------------------------
def _blend(low: RGBColor, high: RGBColor, ratio: float) -> RGBColor:
    ratio = max(0.0, min(1.0, ratio))

    return RGBColor(
        *(
            int(round(low[index] + (high[index] - low[index]) * ratio))
            for index in range(3)
        )
    )


def _relative_luminance(color: RGBColor) -> float:
    """粗略亮度，用來決定文字要黑要白。"""
    return (
        0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    ) / 255.0


def heatmap_color(
    value: float | None,
    minimum: float,
    maximum: float,
) -> RGBColor | None:
    """
    依數值在 [minimum, maximum] 中的位置取色。

    缺值回傳 None（不上色）。全表同值時一律取低值色——此時色階沒有
    資訊量，用深色會誤導讀者以為每格都是高點。
    """
    if value is None:
        return None

    if maximum <= minimum:
        return HEATMAP_LOW

    return _blend(HEATMAP_LOW, HEATMAP_HIGH, (value - minimum) / (maximum - minimum))


def _value_range(spec: TableSpec) -> tuple[float, float]:
    values = [
        value
        for series in spec.series.values()
        for value in series
        if value is not None
    ]

    if not values:
        raise TableBuildError(f"表格 {spec.title!r} 沒有任何可用數值")

    return min(values), max(values)


# ---------------------------------------------------------------------------
# 表格生成
# ---------------------------------------------------------------------------
def add_native_table(
    slide: Slide,
    spec: TableSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(8229600),
    height: int = Emu(4114800),
):
    """
    插入 PPT 原生表格（``shapes.add_table``）。

    ``spec.heatmap`` 為 True 時依數值為儲存格上色，模擬熱力圖。

    Returns:
        ``pptx.table.Table``。
    """
    if not spec.categories:
        raise TableBuildError(f"表格 {spec.title!r} 沒有任何資料列")

    if not spec.series:
        raise TableBuildError(f"表格 {spec.title!r} 沒有任何資料欄")

    if len(spec.categories) > MAX_TABLE_ROWS:
        raise TableBuildError(
            f"表格 {spec.title!r} 有 {len(spec.categories)} 列，"
            f"超過單頁可閱讀上限 {MAX_TABLE_ROWS} 列。"
            "請先取 Top N，或改用圖表呈現。"
        )

    column_names = list(spec.series)
    row_count = len(spec.categories) + 1
    column_count = len(column_names) + 1

    graphic_frame = slide.shapes.add_table(
        row_count, column_count, left, top, width, height
    )
    table = graphic_frame.table

    minimum, maximum = _value_range(spec) if spec.heatmap else (0.0, 0.0)

    _write_header(table, spec, column_names)

    for row_offset, category in enumerate(spec.categories, start=1):
        emphasize = category in spec.emphasize_rows
        _write_label_cell(table.cell(row_offset, 0), category, emphasize)

        for column_offset, name in enumerate(column_names, start=1):
            values = spec.series[name]
            value = values[row_offset - 1] if row_offset - 1 < len(values) else None

            _write_value_cell(
                table.cell(row_offset, column_offset),
                value,
                spec,
                emphasize=emphasize,
                heat=(
                    heatmap_color(value, minimum, maximum)
                    if spec.heatmap
                    else None
                ),
            )

    return table


def _write_header(table, spec: TableSpec, column_names: Sequence[str]) -> None:
    headers = [spec.row_header, *column_names]

    for index, text in enumerate(headers):
        cell = table.cell(0, index)
        cell.text = str(text)
        cell.fill.solid()
        cell.fill.fore_color.rgb = HEADER_FILL

        for paragraph in cell.text_frame.paragraphs:
            for run in paragraph.runs:
                run.font.bold = True
                run.font.size = HEADER_FONT_SIZE
                run.font.color.rgb = HEADER_FONT_COLOR


def _write_label_cell(cell, text: str, emphasize: bool) -> None:
    cell.text = str(text)

    for paragraph in cell.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = BODY_FONT_SIZE
            run.font.bold = emphasize


def _write_value_cell(
    cell,
    value: float | None,
    spec: TableSpec,
    *,
    emphasize: bool,
    heat: RGBColor | None,
) -> None:
    cell.text = format_value(value, spec.unit)

    if heat is not None:
        cell.fill.solid()
        cell.fill.fore_color.rgb = heat

    font_color = (
        HEADER_FONT_COLOR
        if heat is not None
        and _relative_luminance(heat) < _DARK_BACKGROUND_THRESHOLD
        else None
    )

    for paragraph in cell.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = BODY_FONT_SIZE
            run.font.bold = emphasize

            if font_color is not None:
                run.font.color.rgb = font_color


def add_heatmap_table(
    slide: Slide,
    spec: TableSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(8229600),
    height: int = Emu(4114800),
):
    """
    熱力圖：原生表格 + 資料驅動的儲存格底色。

    這**不是**真正的圖表物件，使用者右鍵沒有「編輯資料」選項——
    PowerPoint 沒有熱力圖類型，這是規格書 FR-2.3 明訂的替代方案。
    """
    heat_spec = TableSpec(
        title=spec.title,
        categories=spec.categories,
        series=spec.series,
        heatmap=True,
        row_header=spec.row_header,
        unit=spec.unit,
        emphasize_rows=spec.emphasize_rows,
    )

    return add_native_table(slide, heat_spec, left, top, width, height)


#: 表格類 skill registry。與 CHART_SKILLS 分開註冊，因為它們的驗收標準不同：
#: 圖表要能右鍵編輯資料，表格不能（也不該假裝可以）。
TABLE_SKILLS = {
    "table": add_native_table,
    "heatmap": add_heatmap_table,
}


TABLE_SKILL_TOOL_SCHEMAS = [
    {
        "name": "table",
        "description": (
            "PPT 原生表格。適合需要精確讀出每一格數字的場合"
            "（如風險指標對照、Top N 明細）。列數上限 "
            f"{MAX_TABLE_ROWS}，超過請先取 Top N。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric_key": {
                    "type": "string",
                    "description": "MetricStore 中的指標鍵，不可填入實際數值。",
                },
                "series_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要列出的系列（表格的欄），留空代表全取。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
    {
        "name": "heatmap",
        "description": (
            "熱力圖：原生表格 + 資料驅動的儲存格底色。"
            "適合多實體 × 多期間的強弱分佈一眼比較。"
            "注意這不是圖表物件，右鍵沒有「編輯資料」。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "metric_key": {
                    "type": "string",
                    "description": "MetricStore 中的指標鍵，不可填入實際數值。",
                },
                "series_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要上色的系列（欄），留空代表全取。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
]
"""給 LLM 的表格 skill schema。與圖表一樣沒有數值欄位。"""
