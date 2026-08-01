"""
簡報視覺主題
=============
對齊 `source/附件三_信用卡範例簡報及錯誤說明.pptx` 的版面語彙，是整個
`ppt_generation` 唯一的顏色／字體／幾何來源。任何模組要用顏色或字級，
一律從這裡取，不再各自硬編——同一份簡報出現兩種灰、兩種字級，
是最容易發生也最沒必要的瑕疵。

放在 :mod:`core` 而非 :mod:`output`，是因為 :mod:`charts` 與 :mod:`output`
都要用它。若留在 ``output/``，``charts`` 匯入它會經過 ``output/__init__``
→ ``renderer`` → ``charts``，形成循環匯入。

## 附件三的版面語彙（實測解析結果）

| 元素 | 附件三的做法 |
|---|---|
| 標題 | 左上、22.5pt、深灰 `1A1A1A`、微軟正黑體，不使用置中大標 |
| 重點訊息帶 | 標題下方一條全寬色塊，內含一行「◆ 結論句」，粗體 |
| 主體文字 | 13.5pt / 10.5pt 兩級，中灰 `4D4D4D`，標籤用 `666666` |
| 品牌強調 | 台新紅 `C12026`，用於章節編號、關鍵數字、行動項目符號 |
| 章節頁 | `CHAPTER 0N`（紅）+ 章節名（40pt 粗體灰）+ 紅色底線 |
| 圖表頁註記 | 頁尾一行「可點選圖表右鍵『編輯資料』查看原始 EXCEL 資料表」 |

## 黃色的用途（本專案的決定）

附件三本身用黃底 `FFFF00` 標示「要讀者特別注意的地方」（命題方的錯誤
批註與那句右鍵提示）。本專案沿用這個語彙，但把它指向真正的重點：

- **重點訊息帶**：淡黃底 + 左側黃色標示條，放該頁的結論句
- **敘事中的數字**：凡是由 MetricStore 代入的數值，一律加黃色 highlight

第二項同時是一個可視化的稽核線索——螢光筆標到的每一個字元都來自
MetricStore 查表，不是 LLM 寫出來的。沒被標到的數字就是漏網之魚。

台新紅保留給品牌與結構性強調（章節編號、底線、項目符號），與黃色分工，
不互相搶。

## 圖表配色（本專案的決定）

**不使用模板主題的 accent1..accent6 預設調色盤。** 那套調色盤讓一張圖出現
藍、橘、灰、黃四種不相干的顏色，讀者無法從顏色本身讀出任何資訊，而且與
台新紅的品牌語彙互相打架。

改用單色階（monochrome）配色，一份簡報只有兩個色系：

- **白 → 台新紅漸層**（:data:`CHART_RAMP_LOW` → :data:`CHART_RAMP_HIGH`）：
  用於「同一個指標、多個類別」的比較。顏色深淺直接編碼數值大小，
  深紅即高值，這是顏色唯一該承載的資訊。
- **近黑**（:data:`CHART_NEUTRAL`）：多系列圖表的第二系列。紅配黑對比足夠，
  且列印成灰階後仍可區分。

漸層低端刻意不取純白（:data:`CHART_RAMP_LOW` 是極淺的紅色調）——投影片
底色就是白色，純白的長條等於沒有長條。

## 字級（本專案的決定）

字級不是常數，是**算出來的**。同一個文字框在不同頁面要裝的字數差三倍，
用固定字級的結果就是「短的看起來空、長的擠到溢出」。

:func:`fit_font_size` 依「文字長度」與「可用框寬高」推算字級，
夾在該用途的上下限之間。下方的 ``*_FONT_SIZE`` 常數因此有兩種角色：
沒有長度變數的元素（頁尾、章節編號）直接使用，有長度變數的元素
（標題、要點、重點訊息帶）當作 :func:`fit_font_size` 的上限。
"""

from __future__ import annotations

import math

from pptx.dml.color import RGBColor
from pptx.util import Emu, Pt


# ---------------------------------------------------------------------------
# 顏色
# ---------------------------------------------------------------------------
#: 台新品牌紅。附件三全篇的強調色（89 處），用於章節編號、底線、項目符號。
ACCENT = RGBColor(0xC1, 0x20, 0x26)

#: 黃色重點。附件三用它標示「請特別看這裡」。
HIGHLIGHT = RGBColor(0xFF, 0xFF, 0x00)

#: 重點訊息帶底色（淡黃）。整條純黃太搶眼，會蓋掉圖表；
#: 淡底 + 左側純黃標示條的組合，遠看仍是一條黃帶。
KEY_BAR_FILL = RGBColor(0xFF, 0xF8, 0xD6)

#: 重點訊息帶左側的標示條，用純黃補足「螢光筆」的視覺重量。
KEY_BAR_MARKER = RGBColor(0xFF, 0xC0, 0x00)

#: 文字顏色三級：標題 / 主體 / 次要標籤。取自附件三實測值。
TITLE_COLOR = RGBColor(0x1A, 0x1A, 0x1A)
BODY_COLOR = RGBColor(0x4D, 0x4D, 0x4D)
MUTED_COLOR = RGBColor(0x66, 0x66, 0x66)

#: 深底上的反白文字。
INVERSE_COLOR = RGBColor(0xFF, 0xFF, 0xFF)

#: 圖表框線、格線、表格分隔線。淺到只夠界定範圍，不與資料爭視線。
HAIRLINE_COLOR = RGBColor(0xD9, 0xD9, 0xD9)


# ---------------------------------------------------------------------------
# 圖表配色：白 → 台新紅漸層 + 近黑
# ---------------------------------------------------------------------------
#: 漸層低端。不取純白——投影片底色即白，純白長條等於沒有長條。
CHART_RAMP_LOW = RGBColor(0xFB, 0xE4, 0xE5)

#: 漸層高端即台新紅，讓「最高值」與品牌色重合。
CHART_RAMP_HIGH = ACCENT

#: 多系列圖表的第二系列色。與紅色對比足夠，且列印成灰階仍可區分。
CHART_NEUTRAL = RGBColor(0x26, 0x26, 0x26)

#: 第三系列起的中性灰，避免與 :data:`CHART_NEUTRAL` 混淆。
CHART_NEUTRAL_LIGHT = RGBColor(0x8C, 0x8C, 0x8C)

#: 多系列圖表的系列配色順序。紅、黑交錯，相鄰系列一定分得開。
#: 系列數超出長度時由 :func:`series_colors` 回頭走漸層補色。
CHART_SERIES_COLORS = (
    CHART_RAMP_HIGH,
    CHART_NEUTRAL,
    RGBColor(0xE2, 0x7A, 0x7E),
    CHART_NEUTRAL_LIGHT,
)

#: 熱力圖／單系列漸層的兩端。與圖表漸層共用同一組端點，
#: 一份簡報裡「顏色越深＝數值越大」的規則只有一套。
HEATMAP_LOW = RGBColor(0xFF, 0xFF, 0xFF)
HEATMAP_HIGH = CHART_RAMP_HIGH

#: 底色亮度低於此門檻時，文字改為反白以維持可讀性（無障礙要求）。
DARK_BACKGROUND_THRESHOLD = 0.55


def blend(low: RGBColor, high: RGBColor, ratio: float) -> RGBColor:
    """在兩色之間線性插值。``ratio`` 夾在 [0, 1]。"""
    ratio = max(0.0, min(1.0, ratio))

    return RGBColor(
        *(
            int(round(low[index] + (high[index] - low[index]) * ratio))
            for index in range(3)
        )
    )


def relative_luminance(color: RGBColor) -> float:
    """粗略亮度（0=黑，1=白），用來決定疊在上面的文字要黑要白。"""
    return (0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]) / 255.0


def readable_text_color(background: RGBColor) -> RGBColor:
    """回傳在 ``background`` 上仍讀得清楚的文字色。"""
    if relative_luminance(background) < DARK_BACKGROUND_THRESHOLD:
        return INVERSE_COLOR

    return TITLE_COLOR


def ramp_colors(
    count: int,
    *,
    low: RGBColor = CHART_RAMP_LOW,
    high: RGBColor = CHART_RAMP_HIGH,
) -> list[RGBColor]:
    """
    產生 ``count`` 個由淺到深的漸層色。

    單一系列、多個類別的圖表（排名圖、市占率圓餅圖）用它為每個資料點
    上色，讓顏色深淺直接編碼數值大小。

    只有一個資料點時取高端色——一個孤立的極淺色長條看起來像沒有資料。
    """
    if count <= 0:
        return []

    if count == 1:
        return [high]

    return [blend(low, high, index / (count - 1)) for index in range(count)]


def series_colors(count: int) -> list[RGBColor]:
    """
    產生 ``count`` 個系列色。

    前四個系列走 :data:`CHART_SERIES_COLORS` 的紅／黑交錯，之後才回頭用
    漸層補色。四個系列在一張投影片上已是可讀性上限，補色只是防呆，
    不是鼓勵這樣畫。
    """
    if count <= 0:
        return []

    if count <= len(CHART_SERIES_COLORS):
        return list(CHART_SERIES_COLORS[:count])

    extra = count - len(CHART_SERIES_COLORS)

    return [
        *CHART_SERIES_COLORS,
        *ramp_colors(extra, low=CHART_RAMP_LOW, high=RGBColor(0xE2, 0x7A, 0x7E)),
    ]


def rank_ramp_colors(values: list[float | None]) -> list[RGBColor]:
    """
    依 ``values`` 的**大小排名**取漸層色：最大值最深，最小值最淺。

    刻意用排名而非數值比例。市占率這類資料常有一個遠高於其餘的龍頭，
    按比例上色會讓後段全部擠在淺色端而彼此難分；按排名上色則每個切片
    的顏色都不同，同時仍維持「越深越大」的閱讀規則。

    缺值取最淺色——它在圖上本來就沒有長度，顏色不必再強調一次。
    """
    if not values:
        return []

    ramp = ramp_colors(len(values))

    # 由小到大排序後的位置即該值該取的色階索引。
    # 缺值排在最前面，因此落在最淺的色階上。
    order = sorted(
        range(len(values)),
        key=lambda index: (
            values[index] is not None,
            values[index] if values[index] is not None else 0.0,
        ),
    )

    colors = [CHART_RAMP_LOW] * len(values)

    for rank, index in enumerate(order):
        colors[index] = ramp[rank]

    return colors


# ---------------------------------------------------------------------------
# 字體
# ---------------------------------------------------------------------------
#: 附件三全篇的中文字體。英數字混排時 python-pptx 只設 latin，
#: 中文得另外寫入 ``a:ea``，見 :func:`apply_font`。
FONT_NAME = "微軟正黑體"

TITLE_FONT_SIZE = Pt(22)
KEY_MESSAGE_FONT_SIZE = Pt(13)
HEADLINE_FONT_SIZE = Pt(13)
BULLET_FONT_SIZE = Pt(11)
AGENDA_FONT_SIZE = Pt(16)
CHAPTER_LABEL_FONT_SIZE = Pt(14)
CHAPTER_TITLE_FONT_SIZE = Pt(32)
FOOTNOTE_FONT_SIZE = Pt(10)

#: 自適應字級的下限。低於此值投影時已不可讀，寧可讓 PowerPoint 的
#: 溢出自動縮放接手，也不要主動算出一個讀不到的字級。
MIN_TITLE_FONT_SIZE = Pt(14)
MIN_BODY_FONT_SIZE = Pt(9)
MIN_KEY_MESSAGE_FONT_SIZE = Pt(10)

#: 要點文字的上限。附件三的主體文字有 13.5 / 10.5 兩級，
#: :data:`BULLET_FONT_SIZE` 是典型頁面會拿到的字級，這裡則是「這頁只有
#: 三句短要點」時允許長到多大——短敘事停在 11pt 會讓半頁留白，
#: 那正是「有的太小」的來源。
MAX_BODY_FONT_SIZE = Pt(13)

#: 圖表內的文字。圖表字級不隨內容變動，但必須明確設定——不設就繼承
#: 模板主題的 18pt 預設值，在 60% 寬的圖表區裡會把座標軸標籤擠成一團。
CHART_TITLE_FONT_SIZE = Pt(12)
CHART_LABEL_FONT_SIZE = Pt(9)
CHART_LEGEND_FONT_SIZE = Pt(9)
CHART_DATA_LABEL_FONT_SIZE = Pt(9)

#: 表格文字。表格字級另由 :func:`fit_table_font_size` 依列數再往下收。
TABLE_HEADER_FONT_SIZE = Pt(11)
TABLE_BODY_FONT_SIZE = Pt(11)
MIN_TABLE_FONT_SIZE = Pt(7)


# ---------------------------------------------------------------------------
# 自適應字級
# ---------------------------------------------------------------------------
#: 中日韓字元在字級 S 時約占 S pt 寬；西文與數字約占 0.55 S。
#: 兩者混排時用實際比例估算，不用單一係數硬套。
_CJK_WIDTH_RATIO = 1.0
_LATIN_WIDTH_RATIO = 0.55

#: 行高相對字級的倍率（含行距）。
_LINE_HEIGHT_RATIO = 1.32

#: 段距（``space_after``）相對字級的倍率。renderer 寫入的段距用同一個比例，
#: 兩邊必須一致，否則估算與實際排版會愈差愈多。
_PARAGRAPH_GAP_RATIO = 0.7

#: 文字框左右內縮與上下內縮的預設值（EMU），估算可用寬高時要扣掉。
_DEFAULT_INSET_X = Emu(91440 * 2)
_DEFAULT_INSET_Y = Emu(45720 * 2)

#: 1 pt = 12700 EMU。
_EMU_PER_PT = 12700


def paragraph_gap_for(size):
    """
    某字級對應的段距（``space_after``）。

    ``fit_font_size`` 用 :data:`_PARAGRAPH_GAP_RATIO` 估算段距吃掉的高度，
    實際寫入時必須用同一個比例，否則估算永遠對不上排版結果。
    """
    return Pt(int(size) / _EMU_PER_PT * _PARAGRAPH_GAP_RATIO)


def _visual_width(text: str) -> float:
    """把字串換算成「相當於幾個中文字寬」。"""
    width = 0.0

    for char in text:
        # CJK 統一漢字、全角標點、注音、假名一律算全寬。
        if ord(char) > 0x2E7F:
            width += _CJK_WIDTH_RATIO
        else:
            width += _LATIN_WIDTH_RATIO

    return width


def fit_font_size(
    text: str,
    width: int,
    height: int,
    *,
    maximum: Pt,
    minimum: Pt,
    paragraph_count: int = 1,
    inset_x: int | None = None,
    inset_y: int | None = None,
):
    """
    依文字長度與可用框大小推算字級。

    做法是解「在字級 S 時這段文字要幾行、這幾行放不放得下」：

    1. 一行可容納的字寬數 = 可用寬 / S
    2. 需要的行數 = 文字總字寬 / 每行字寬數
    3. 需要的高度 = 行數 × S × 行高倍率 + 段距總和

    直接解不等式會落在無理數上，改用單調性逐級試：字級越大需要的高度越大，
    因此從 ``maximum`` 往下逐級（0.5pt）試，取第一個放得下的。級距只有
    ``(maximum - minimum) / 0.5`` 階，最多數十次迴圈，成本可忽略。

    回傳值一定落在 ``[minimum, maximum]``。放不下 ``minimum`` 時仍回傳
    ``minimum``——此時交給文字框的溢出自動縮放收尾，總比算出 4pt 好。

    Args:
        text: 要放進去的完整文字（多段請先接起來）。
        width: 可用寬度（EMU）。
        height: 可用高度（EMU）。
        maximum: 字級上限，也是短文字會拿到的字級。
        minimum: 字級下限。
        paragraph_count: 段落數。段距也吃高度，五個要點的段距加起來
            相當於半行字，不計入會高估可用空間。
        inset_x: 左右內縮總量（EMU），預設為 PowerPoint 的預設邊界。
        inset_y: 上下內縮總量（EMU）。

    Returns:
        ``pptx.util.Pt`` 字級。
    """
    demand = _visual_width(text)

    if demand <= 0:
        return maximum

    usable_width_pt = (
        width - (int(_DEFAULT_INSET_X) if inset_x is None else inset_x)
    ) / _EMU_PER_PT
    usable_height_pt = (
        height - (int(_DEFAULT_INSET_Y) if inset_y is None else inset_y)
    ) / _EMU_PER_PT

    if usable_width_pt <= 0 or usable_height_pt <= 0:
        return minimum

    max_pt = int(maximum) / _EMU_PER_PT
    min_pt = int(minimum) / _EMU_PER_PT

    candidate = max_pt

    while candidate > min_pt:
        chars_per_line = usable_width_pt / candidate

        if chars_per_line >= 1:
            # 每行都會被填滿才換行，所以行數是「進位」而非四捨五入。
            lines = math.ceil(demand / chars_per_line)
            needed = (
                lines * candidate * _LINE_HEIGHT_RATIO
                + max(paragraph_count - 1, 0) * candidate * _PARAGRAPH_GAP_RATIO
            )

            if needed <= usable_height_pt:
                break

        candidate -= 0.5

    return Pt(max(candidate, min_pt))


def fit_single_line_font_size(
    text: str,
    width: int,
    *,
    maximum: Pt,
    minimum: Pt,
    inset_x: int | None = None,
):
    """
    推算「必須排成一行」的文字字級（標題、重點訊息帶）。

    與 :func:`fit_font_size` 的差別是不允許換行：這些元素的框高是固定的，
    換行就是溢出。因此只解寬度，且直接算出精確值再往下取到 0.5pt。
    """
    demand = _visual_width(text)

    if demand <= 0:
        return maximum

    usable_width_pt = (
        width - (int(_DEFAULT_INSET_X) if inset_x is None else inset_x)
    ) / _EMU_PER_PT

    if usable_width_pt <= 0:
        return minimum

    max_pt = int(maximum) / _EMU_PER_PT
    min_pt = int(minimum) / _EMU_PER_PT

    fitted = usable_width_pt / demand
    # 往下取到 0.5pt，避免回傳 11.37pt 這種在 PowerPoint 介面上看起來
    # 像是誤設的值。
    fitted = int(fitted * 2) / 2

    return Pt(max(min(fitted, max_pt), min_pt))


def fit_table_font_size(row_count: int, column_count: int, height: int):
    """
    依表格列數與可用高度推算儲存格字級。

    表格的溢出方式和文字框不同：列高會被內容撐開，撐到超出投影片為止，
    而且 PowerPoint 不會自動縮小表格文字。所以這裡必須主動算。

    欄數只用來再往下收一級——欄多時每欄變窄，長數字會被迫折行，
    折行後的列高又比估算值高。
    """
    if row_count <= 0:
        return TABLE_BODY_FONT_SIZE

    usable_height_pt = height / _EMU_PER_PT
    # 儲存格上下邊界約 0.05 吋 × 2，換算後每列額外約 7pt。
    per_row_pt = usable_height_pt / row_count - 7.0

    max_pt = int(TABLE_BODY_FONT_SIZE) / _EMU_PER_PT
    min_pt = int(MIN_TABLE_FONT_SIZE) / _EMU_PER_PT

    fitted = per_row_pt / _LINE_HEIGHT_RATIO

    if column_count > 6:
        fitted -= 1.0

    fitted = int(fitted * 2) / 2

    return Pt(max(min(fitted, max_pt), min_pt))


# ---------------------------------------------------------------------------
# 幾何（EMU）
# ---------------------------------------------------------------------------
#: 重點訊息帶高度與其下方留給內容的間距。
KEY_BAR_HEIGHT = Emu(457200)
KEY_BAR_GAP = Emu(150000)

#: 重點訊息帶左側標示條寬度。
KEY_BAR_MARKER_WIDTH = Emu(57150)

#: 標示條與文字之間的留白。
KEY_BAR_TEXT_INSET = Emu(175260)

#: 頁尾註記位置（貼齊附件三的 y 座標）。
FOOTNOTE_TOP = Emu(6150000)
FOOTNOTE_HEIGHT = Emu(250000)

#: 章節頁紅色底線。
CHAPTER_RULE_WIDTH = Emu(1905000)
CHAPTER_RULE_HEIGHT = Emu(28575)

#: 重點訊息帶前綴。附件三每頁的結論句都以此開頭。
KEY_MESSAGE_PREFIX = "◆ "

#: 行動項目符號（附件三的建議條列用它）。
ACTION_BULLET_PREFIX = "▸ "

#: 圖表頁的頁尾註記。命題要求的驗收動作寫在簡報上，讀者不必被口頭告知。
CHART_FOOTNOTE = "可點選圖表右鍵「編輯資料」查看原始 EXCEL 資料表"

#: 表格頁的頁尾註記。表格沒有內嵌工作簿，右鍵不會有「編輯資料」——
#: 沿用圖表那句就是騙人，這裡明確指向稽核用的 .xlsx。
TABLE_FOOTNOTE = "本表數值與隨附 Excel 稽核檔逐格一致"


def apply_font(
    run,
    *,
    size=None,
    bold: bool | None = None,
    color: RGBColor | None = None,
    highlight: RGBColor | None = None,
    name: str = FONT_NAME,
) -> None:
    """
    設定一個 run 的字體。

    python-pptx 的 ``font.name`` 只寫 ``a:latin``，中文會落回主題字體，
    造成同一行中英文字重不一致。這裡連 ``a:ea``（東亞）與 ``a:cs``
    一起寫，與附件三的做法相同。

    ``highlight`` 走 ``a:highlight``（螢光筆），這是 PowerPoint 的文字
    標示，不是文字框底色——所以只有被標的字元變黃，段落其餘部分不受影響。
    """
    _apply_font_properties(
        run.font,
        run._r.get_or_add_rPr(),
        size=size,
        bold=bold,
        color=color,
        highlight=highlight,
        name=name,
    )


def apply_chart_font(
    font,
    *,
    size=None,
    bold: bool | None = None,
    color: RGBColor | None = None,
    name: str = FONT_NAME,
) -> None:
    """
    設定圖表元素的字體（圖表標題、座標軸標籤、圖例、資料標籤）。

    圖表元素給的是 ``pptx.text.text.Font``（包 ``a:defRPr``）而不是 run，
    但東亞字體要另外寫 ``a:ea`` 的問題完全相同——不寫的話，座標軸上的
    中文銀行名稱會落回主題字體，與投影片其餘文字字重不一致。
    """
    _apply_font_properties(
        font,
        font._rPr,
        size=size,
        bold=bold,
        color=color,
        highlight=None,
        name=name,
    )


def _apply_font_properties(
    font,
    rpr,
    *,
    size,
    bold: bool | None,
    color: RGBColor | None,
    highlight: RGBColor | None,
    name: str,
) -> None:
    """:func:`apply_font` 與 :func:`apply_chart_font` 的共用實作。"""
    font.name = name

    if size is not None:
        font.size = size

    if bold is not None:
        font.bold = bold

    if color is not None:
        font.color.rgb = color

    for tag in ("a:ea", "a:cs"):
        element = rpr.find(_qn(tag))

        if element is None:
            element = rpr.makeelement(_qn(tag), {})
            rpr.append(element)

        element.set("typeface", name)

    if highlight is not None:
        _set_highlight(rpr, highlight)


def _qn(tag: str) -> str:
    from pptx.oxml.ns import qn

    return qn(tag)


def _set_highlight(rpr, color: RGBColor) -> None:
    """
    寫入 ``<a:highlight><a:srgbClr val="FFFF00"/></a:highlight>``。

    ``a:highlight`` 在 rPr 中的位置有 schema 順序要求（必須排在
    ``a:solidFill`` 之後、``a:latin`` 之前）。python-pptx 沒有高階 API，
    但這裡只碰 rPr 的子元素順序，不影響任何圖表或內嵌工作簿。
    """
    existing = rpr.find(_qn("a:highlight"))

    if existing is not None:
        rpr.remove(existing)

    highlight = rpr.makeelement(_qn("a:highlight"), {})
    srgb = rpr.makeelement(_qn("a:srgbClr"), {"val": f"{color}"})
    highlight.append(srgb)

    # 插在字體宣告之前；找不到就接在最後（rPr 只有 solidFill 的情況）。
    anchor = None

    for tag in ("a:latin", "a:ea", "a:cs"):
        anchor = rpr.find(_qn(tag))

        if anchor is not None:
            break

    if anchor is None:
        rpr.append(highlight)
    else:
        anchor.addprevious(highlight)
