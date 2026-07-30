"""
簡報視覺主題
=============
對齊 `source/附件三_信用卡範例簡報及錯誤說明.pptx` 的版面語彙，是整個
`ppt_generation` 唯一的顏色／字體／幾何來源。任何模組要用顏色或字級，
一律從這裡取，不再各自硬編——同一份簡報出現兩種灰、兩種字級，
是最容易發生也最沒必要的瑕疵。

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
"""

from __future__ import annotations

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
    font = run.font
    font.name = name

    if size is not None:
        font.size = size

    if bold is not None:
        font.bold = bold

    if color is not None:
        font.color.rgb = color

    rpr = run._r.get_or_add_rPr()

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
