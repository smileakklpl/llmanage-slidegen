"""
圖表生成模組 (POC)
=====================
核心設計原則：
1. 圖表數字的「唯一真相來源」是本模組的輸入資料（解析後的 Excel 結構化資料）。
2. 一律透過 python-pptx 的 add_chart() API 生成圖表，
   讓「畫面顯示值 (chart cache)」與「右鍵編輯資料看到的內嵌工作表」
   自動保持一致，不手動操作底層 XML。
3. 不使用外部連結 (external link) 方式綁定 Excel，
   因為 PPT 寄出後外部路徑會失效。改用 PowerPoint 原生的
   「內嵌工作簿 (embedded workbook)」機制，這正是使用者
   右鍵「編輯資料」時開啟的資料來源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.oxml.ns import qn
from pptx.slide import Slide
from pptx.util import Emu

from lxml import etree


@dataclass
class ChartSpec:
    """一張圖表所需的資料規格。"""

    title: str
    categories: Sequence[str]
    series: dict[str, Sequence[float]]  # {系列名稱: 數值}
    chart_type: XL_CHART_TYPE = XL_CHART_TYPE.COLUMN_CLUSTERED


def add_category_chart(
    slide: Slide,
    spec: ChartSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(8229600),
    height: int = Emu(4114800),
):
    """
    將 ChartSpec 的資料寫入圖表，並插入到指定投影片。

    重點：CategoryChartData 是唯一資料入口。
    python-pptx 會同時：
      1. 寫入 chart1.xml 的 <c:numCache>（畫面顯示用）
      2. 產生對應的 embedded .xlsx（右鍵「編輯資料」開啟用）
    兩者保證數值一致，因為都是從這裡的同一份 spec 產生。
    """
    chart_data = CategoryChartData()
    chart_data.categories = list(spec.categories)
    for series_name, values in spec.series.items():
        chart_data.add_series(series_name, values)

    graphic_frame = slide.shapes.add_chart(
        spec.chart_type, left, top, width, height, chart_data
    )
    chart = graphic_frame.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = spec.title
    return chart


@dataclass
class ScatterSpec:
    """散點圖（規模 vs 成長）專用規格。"""

    title: str
    series_name: str
    points: Sequence[tuple[float, float]]  # [(x, y), ...]
    labels: Sequence[str] | None = None  # 對應每個點的標籤（如銀行名稱）


def add_scatter_chart(
    slide: Slide,
    spec: ScatterSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(8229600),
    height: int = Emu(4114800),
):
    """
    散點圖走 XyChartData，一樣是唯一資料入口原則。
    labels 部分 python-pptx 原生不支援資料點標籤文字，
    若模板要求顯示銀行名稱標籤，需要在 chart 生成後
    透過底層 XML（c:dLbls）額外處理，這裡先留 TODO。
    """
    chart_data = XyChartData()
    series = chart_data.add_series(spec.series_name)
    for x, y in spec.points:
        series.add_data_point(x, y)

    graphic_frame = slide.shapes.add_chart(
        XL_CHART_TYPE.XY_SCATTER, left, top, width, height, chart_data
    )
    chart = graphic_frame.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = spec.title
    # TODO: 若需散點標籤（如銀行名稱），需手動操作 chart.plots[0] 的
    # dLbls XML 節點，python-pptx 目前無高階 API。
    return chart


def add_pie_chart(
    slide: Slide,
    spec: ChartSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(6096000),
    height: int = Emu(4114800),
):
    """
    市占率圖（圓餅圖）。走同一個 CategoryChartData 入口，
    僅 chart_type 固定為 PIE。要求 spec.series 只能有一組系列。
    """
    if len(spec.series) != 1:
        raise ValueError("圓餅圖只能有一組系列，請確認 ChartSpec.series 長度為 1")
    pie_spec = ChartSpec(
        title=spec.title,
        categories=spec.categories,
        series=spec.series,
        chart_type=XL_CHART_TYPE.PIE,
    )
    return add_category_chart(slide, pie_spec, left, top, width, height)


# ---------------------------------------------------------------------------
# 雙軸圖（長條 + 折線，次要數值軸）
# ---------------------------------------------------------------------------
# 這是 python-pptx 唯一沒有高階 API 的必做圖型（附件三 P.5/P.6 官方主打頁：
# 流通卡數長條 + 簽帳金額折線，兩者量級差一個數量級，非用次軸不可）。
#
# 做法上有兩條路，只有一條是對的：
#
# ✗ 自己組 chart XML 並手填 <c:numCache> —— 快取值會有，但內嵌 workbook 不會有，
#   使用者右鍵「編輯資料」看到空表，或看到與畫面不符的舊值。這正是附件三的病灶。
#
# ✓ 先用 add_chart() 一次把**所有**系列（含之後要變折線的）寫進去，
#   內嵌 workbook 由 python-pptx 完整寫好；再把後段 <c:ser> 節點從
#   <c:barChart> 搬到新建的 <c:lineChart> 並掛上次軸。
#
# 搬動的是「這個系列用什麼圖形畫」，完全沒有碰任何數值節點，
# 所以三份副本的一致性由 add_chart() 保證，與圖形改造無關。
@dataclass
class ComboSpec(ChartSpec):
    """
    雙軸圖規格。

    ``series`` 的順序即繪製順序；``line_series_names`` 列出的系列會改用折線
    並掛到次要數值軸，其餘留在主軸長條。刻意繼承 :class:`ChartSpec`——
    稽核 Excel 匯出與三方比對都只認 ChartSpec 的形狀，繼承讓它們不必分支。
    """

    line_series_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def column_series_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.series if name not in self.line_series_names
        )


def _next_axis_ids(plot_area, count: int = 2) -> list[int]:
    """產生未被使用的 axId。與現有 id 相撞會讓 PowerPoint 判定檔案損毀。"""
    existing = {
        int(element.get("val"))
        for element in plot_area.iter(qn("c:axId"))
        if element.get("val", "").isdigit()
    }
    candidate = (max(existing) if existing else 100_000_000) + 1

    ids: list[int] = []

    while len(ids) < count:
        if candidate not in existing:
            ids.append(candidate)

        candidate += 1

    return ids


def _sub(parent, tag: str, **attrs):
    element = etree.SubElement(parent, qn(tag))

    for name, value in attrs.items():
        element.set(name, str(value))

    return element


def _append_secondary_axes(plot_area, cat_ax_id: int, val_ax_id: int) -> None:
    """
    加上次軸的 catAx / valAx 配對。

    次軸的類別軸一定要 ``delete=1``：兩組類別軸都畫出來的話，圖底下會出現
    兩排一模一樣的月份標籤。數值軸則放右側（``axPos=r``）。
    """
    cat_ax = etree.SubElement(plot_area, qn("c:catAx"))
    _sub(cat_ax, "c:axId", val=cat_ax_id)
    _sub(_sub(cat_ax, "c:scaling"), "c:orientation", val="minMax")
    _sub(cat_ax, "c:delete", val=1)
    _sub(cat_ax, "c:axPos", val="b")
    _sub(cat_ax, "c:crossAx", val=val_ax_id)

    val_ax = etree.SubElement(plot_area, qn("c:valAx"))
    _sub(val_ax, "c:axId", val=val_ax_id)
    _sub(_sub(val_ax, "c:scaling"), "c:orientation", val="minMax")
    _sub(val_ax, "c:delete", val=0)
    _sub(val_ax, "c:axPos", val="r")
    _sub(val_ax, "c:numFmt", formatCode="General", sourceLinked="0")
    _sub(val_ax, "c:majorTickMark", val="out")
    _sub(val_ax, "c:minorTickMark", val="none")
    _sub(val_ax, "c:tickLblPos", val="nextTo")
    _sub(val_ax, "c:crossAx", val=cat_ax_id)


def _promote_series_to_line(chart, line_indices: Sequence[int]) -> None:
    """
    把指定索引的 ``c:ser`` 從 barChart 搬到新建的 lineChart，並掛上次軸。

    ``line_indices`` 是系列在 barChart 中的位置（0 起算），對應 ComboSpec
    中 ``series`` 的順序。
    """
    plot_area = chart._chartSpace.find(qn("c:chart")).find(qn("c:plotArea"))
    bar_chart = plot_area.find(qn("c:barChart"))

    if bar_chart is None:
        raise ValueError(
            "找不到 c:barChart，雙軸圖必須以 COLUMN_CLUSTERED 為底圖建立"
        )

    all_series = bar_chart.findall(qn("c:ser"))
    moving = [all_series[index] for index in line_indices]

    if len(moving) == len(all_series):
        raise ValueError("雙軸圖至少要留一個系列在長條主軸上")

    cat_ax_id, val_ax_id = _next_axis_ids(plot_area)

    line_chart = etree.Element(qn("c:lineChart"))
    _sub(line_chart, "c:grouping", val="standard")
    _sub(line_chart, "c:varyColors", val=0)

    for ser in moving:
        bar_chart.remove(ser)

        # invertIfNegative 是長條圖專屬子元素，留在折線 ser 裡會違反
        # DrawingML schema，PowerPoint 會直接判定檔案需要修復。
        for tag in ("c:invertIfNegative",):
            child = ser.find(qn(tag))

            if child is not None:
                ser.remove(child)

        marker = etree.Element(qn("c:marker"))
        _sub(marker, "c:symbol", val="circle")
        _sub(marker, "c:size", val=7)

        # marker 在 CT_LineSer 中的位置在 spPr 之後、cat 之前。
        anchor = ser.find(qn("c:cat"))

        if anchor is not None:
            anchor.addprevious(marker)
        else:
            ser.append(marker)

        line_chart.append(ser)
        _sub(ser, "c:smooth", val=0)

    _sub(line_chart, "c:marker", val=1)
    _sub(line_chart, "c:axId", val=cat_ax_id)
    _sub(line_chart, "c:axId", val=val_ax_id)

    # CT_PlotArea 的元素順序：layout → 各圖形群組 → 各軸 → dTable → spPr。
    # lineChart 必須緊接在 barChart 之後，軸則接在既有軸之後。
    bar_chart.addnext(line_chart)
    _append_secondary_axes(plot_area, cat_ax_id, val_ax_id)


def add_combo_chart(
    slide: Slide,
    spec: ComboSpec,
    left: int = Emu(914400),
    top: int = Emu(1600200),
    width: int = Emu(8229600),
    height: int = Emu(4114800),
):
    """
    雙軸圖：部分系列畫長條（主軸），部分畫折線（次軸）。

    資料入口仍然是 :func:`add_category_chart` 的 ``add_chart()``——
    先讓 python-pptx 把全部系列與內嵌 workbook 寫好，再改變後段系列的
    圖形與所屬軸。全程不觸碰任何數值節點。
    """
    if not spec.line_series_names:
        raise ValueError(
            "雙軸圖需指定 line_series_names（要畫成折線並掛次軸的系列）"
        )

    series_names = list(spec.series)
    unknown = [
        name for name in spec.line_series_names if name not in spec.series
    ]

    if unknown:
        raise ValueError(
            f"line_series_names {unknown} 不在 series 中，"
            f"可用系列：{series_names}"
        )

    if len(spec.line_series_names) >= len(series_names):
        raise ValueError(
            "雙軸圖至少要留一個系列畫長條，"
            f"目前 {len(series_names)} 個系列全被指定為折線"
        )

    base = ChartSpec(
        title=spec.title,
        categories=spec.categories,
        series=spec.series,
        chart_type=XL_CHART_TYPE.COLUMN_CLUSTERED,
    )
    chart = add_category_chart(slide, base, left, top, width, height)

    _promote_series_to_line(
        chart,
        [
            index
            for index, name in enumerate(series_names)
            if name in spec.line_series_names
        ],
    )

    return chart


# ---------------------------------------------------------------------------
# Skill Registry：圖表 Agent 只透過 skill 名稱字串呼叫，不需知道實作細節。
# 新增圖表類型時，在此註冊即可，Agent 端無需改動。
# ---------------------------------------------------------------------------
CHART_SKILLS = {
    "column": add_category_chart,   # 長條圖／排名圖／成長率圖
    "bar": add_category_chart,      # 橫條圖
    "line": add_category_chart,     # 折線圖／時間趨勢圖
    "pie": add_pie_chart,           # 市占率圖
    "scatter": add_scatter_chart,   # 規模 vs 成長散點圖
    "combo": add_combo_chart,       # 雙軸圖：長條（主軸）+ 折線（次軸）
    # "heatmap": 由原生表格 + 儲存格底色模擬，不走 add_chart()，
    #            另外註冊在 table_builder.py（尚未實作），Agent 需個別處理。
}

#: skill 名稱 → python-pptx 圖表類型。
#: chart_planner 依此設定 ChartSpec.chart_type，讓 CHART_SKILLS 中共用
#: add_category_chart 的多個 skill（column/bar/line）產生不同圖形。
CHART_TYPE_BY_SKILL = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "pie": XL_CHART_TYPE.PIE,
    "scatter": XL_CHART_TYPE.XY_SCATTER,
    # 雙軸圖以長條為底圖建立，折線系列在 add_chart() 之後才改造。
    "combo": XL_CHART_TYPE.COLUMN_CLUSTERED,
}


# ---------------------------------------------------------------------------
# Function Calling / Tool Use 對應層
# ---------------------------------------------------------------------------
# LLM API（OpenAI / Anthropic / Bedrock 等）本身無法執行 Python 檔案，
# 它只能根據我們提供的 "tool schema" 回傳一段結構化 JSON，
# 表達「我想呼叫哪個 skill、帶哪些參數」。
#
# 真正的執行流程是：
#   1. 我們把 CHART_SKILLS 的 key 轉成 tool schema，隨 prompt 送給 LLM
#   2. LLM 回傳 {"name": "pie", "arguments": {...}}（純文字/JSON，不是程式碼）
#   3. 我們自己的 dispatcher（如下 dispatch_chart_skill）收到這段 JSON，
#      在本地 Python 環境查表、驗證、實際呼叫對應函式
#
# LLM 從未執行過任何一行本地程式碼，執行權限完全留在我方。
# ---------------------------------------------------------------------------

CHART_SKILL_TOOL_SCHEMAS = [
    {
        "name": "column",
        "description": "長條圖，適合排名、成長率比較等類別型數據。",
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
                    "description": "限定取用的系列名稱，留空代表全取。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
    {
        "name": "bar",
        "description": (
            "橫條圖，適合類別名稱較長的排名比較（如銀行名稱橫向排列）。"
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
                    "description": "限定取用的系列名稱，留空代表全取。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
    {
        "name": "line",
        "description": (
            "折線圖，適合時間序列趨勢。僅可用於 axis_kind 為 temporal 的指標。"
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
                    "description": "限定取用的系列名稱，留空代表全取。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
    {
        "name": "pie",
        "description": "圓餅圖，適合市占率等單一系列的佔比數據。",
        "parameters": {
            "type": "object",
            "properties": {
                "metric_key": {"type": "string"},
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "chart_title"],
        },
    },
    {
        "name": "combo",
        "description": (
            "雙軸圖（長條 + 折線）。適合兩個量級差距大、但需並列比較的系列，"
            "例如流通卡數（張）與簽帳金額（百萬元）同時看趨勢。"
            "series_names 的第一個畫長條掛主軸，其餘畫折線掛右側次軸。"
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
                    "description": (
                        "至少兩個系列名稱。第一個為長條（主軸），"
                        "其餘為折線（次軸）。"
                    ),
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "series_names", "chart_title"],
        },
    },
    {
        "name": "scatter",
        "description": "散點圖，適合「規模 vs 成長」等雙變數關係。",
        "parameters": {
            "type": "object",
            "properties": {
                "metric_key": {"type": "string"},
                "series_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "恰好兩個系列名稱，分別對應 x 軸與 y 軸。",
                },
                "chart_title": {"type": "string"},
            },
            "required": ["metric_key", "series_names", "chart_title"],
        },
    },
]
"""
給 LLM API 的 tool schema 清單。注意 schema 中完全沒有 "values" 這種
數字欄位，LLM 只能填 metric_key 引用，這是刻意設計，逼迫 LLM 無法
自己編造數字（呼應 .kiro/steering/tech.md 第 1 條原則）。

實際串接時（OpenAI 範例）：

    response = client.chat.completions.create(
        model=...,
        messages=[...],
        tools=[{"type": "function", "function": schema}
               for schema in CHART_SKILL_TOOL_SCHEMAS],
    )
    tool_call = response.choices[0].message.tool_calls[0]
    skill_name = tool_call.function.name          # 例如 "pie"
    raw_args = json.loads(tool_call.function.arguments)

Anthropic / Bedrock 的 tool use 格式略有不同（key 名稱不同），
但核心流程一致：LLM 回傳「工具名稱 + 參數」，執行仍在本地端。
"""


def dispatch_chart_skill(skill_name: str) -> callable:
    """
    Dispatcher：LLM 回傳的 skill_name（純字串）透過這裡查表，
    取得實際的 Python 函式並在本地執行。

    這是 LLM 輸出與本地程式碼執行之間唯一的橋接點，
    也是防呆的關卡：LLM 若回傳未註冊的名稱，直接拋錯，
    不會有機會執行任何非預期的程式碼路徑
    （因為只從 CHART_SKILLS 這個白名單裡取值，不會 eval/exec LLM 的輸出）。
    """
    skill = CHART_SKILLS.get(skill_name)
    if skill is None:
        raise ValueError(
            f"LLM 回傳了未註冊的 skill: {skill_name!r}，"
            f"可用選項: {list(CHART_SKILLS.keys())}"
        )
    return skill
