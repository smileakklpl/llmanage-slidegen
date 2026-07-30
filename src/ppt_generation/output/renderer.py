"""
PPT 組裝
=========
對應 docs/圖表原生性與資料同步設計.md Stage 5 與 §9。

職責：套用 `source/template.pptx`，逐頁組裝

1. 標題（來自 SectionPlan）
2. 原生圖表（一律經 ``CHART_SKILLS`` → ``add_chart()`` 單一入口）
3. 敘事文字（佔位符在此代入實際數值）

版面策略：模板的 `1_標題及內容` 版面只有一個全寬內容區，本模組把它
縮成右側欄位放文字，左側留給圖表，形成顧問簡報常見的「圖左文右」佈局。

**不可退讓的原則**：本模組不得自行寫入任何數字。圖表數值來自
ChartSpec（由 MetricStore 查表產生），敘事數值來自
:func:`placeholders.render_text` 查表代入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from pptx import Presentation
from pptx.util import Emu, Inches, Pt

from ..core import config, placeholders
from ..agents.narrative_writer import PageNarrative
from ..agents.section_planner import SectionPlan
from ..charts.chart_builder import ScatterSpec
from ..charts.chart_planner import VISUAL_SKILLS, ResolvedChart
from ..data.metric_store import MetricStore


logger = logging.getLogger(__name__)

#: 模板中用於內容頁的版面名稱。
CONTENT_LAYOUT_NAME = "1_標題及內容"

#: 模板中用於章節分隔頁的版面名稱。
SECTION_LAYOUT_NAME = "2_章節標題"

#: 模板中用於結尾頁的版面名稱（附件一模板第 5 頁用的就是這個）。
CLOSING_LAYOUT_NAME = "3_標題投影片"

#: 目錄頁標題與結尾頁文字。都可由呼叫端覆寫。
AGENDA_TITLE = "目錄"
CLOSING_MESSAGE = "感謝聆聽"

#: 封面頁在模板中是第 1 張投影片，保留不刪；其後的示範頁移除。
#: 「封面 + 目錄」共 2 張非內容頁，是頁碼推算的固定前綴。
FRONT_MATTER_SLIDES = 2

#: placeholder 索引（由模板結構決定，見 template.pptx 版面配置）。
PH_TITLE = 0
PH_BODY = 1

#: 圖表占內容區寬度的比例，其餘留給敘事文字。
CHART_WIDTH_RATIO = 0.60


class RenderError(RuntimeError):
    """PPT 組裝失敗。"""


@dataclass
class PageBundle:
    """一頁所需的全部素材。"""

    section: SectionPlan
    chart: ResolvedChart | None = None
    narrative: PageNarrative | None = None


@dataclass
class RenderReport:
    """組裝結果摘要。"""

    output_path: Path | None = None
    page_count: int = 0
    chart_count: int = 0
    #: 佔位符代入失敗的訊息（頁碼 → 錯誤清單）
    placeholder_errors: dict[int, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: 章節分隔頁數量
    divider_count: int = 0
    #: 最終 .pptx 的實際投影片總數（含封面、目錄、章節頁、結尾頁）
    slide_count: int = 0
    #: 依序的章節名稱
    chapters: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 頁碼指派
# ---------------------------------------------------------------------------
def chapter_order(bundles_or_sections: Sequence[Any]) -> list[str]:
    """依出現順序取出章節名稱（跳過未歸章節者）。"""
    chapters: list[str] = []

    for item in bundles_or_sections:
        section = item.section if isinstance(item, PageBundle) else item

        if section.chapter and section.chapter not in chapters:
            chapters.append(section.chapter)

    return chapters


def assign_page_numbers(
    sections: Sequence[SectionPlan],
    *,
    include_agenda: bool = True,
) -> list[SectionPlan]:
    """
    把每個內容頁的 ``page_number`` 改成它在最終 .pptx 中的實際投影片序號。

    這件事必須在圖表決策**之前**做完：稽核 Excel 的工作表名是
    ``P.{頁碼}_{指標名稱}``（FR-3.1），而頁碼是從 ChartPlan 帶下去的。
    若這裡的頁碼是「第幾個內容頁」而不是「第幾張投影片」，主管拿著
    Excel 對照簡報就會翻錯頁——封面、目錄與每張章節分隔頁都會造成偏移。

    版面順序：封面 → 目錄 →（章節分隔頁 → 該章節內容頁…）× N → 結尾頁。

    就地修改並回傳同一批物件，方便鏈式使用。
    """
    # 沒有任何頁面歸屬章節時 render_deck 不會產目錄頁（目錄會是空的），
    # 這裡的推算必須跟著它，否則頁碼會整份差一頁。
    has_chapters = any(section.chapter for section in sections)

    number = 1 + (1 if (include_agenda and has_chapters) else 0)  # 封面（+ 目錄）
    current_chapter: str | None = None

    for section in sections:
        if section.chapter and section.chapter != current_chapter:
            number += 1  # 章節分隔頁
            current_chapter = section.chapter

        number += 1
        section.page_number = number

    return list(sections)


# ---------------------------------------------------------------------------
# 版面計算
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ContentArea:
    """內容區座標（EMU）。由模板的 BODY placeholder 推導。"""

    left: int
    top: int
    width: int
    height: int

    def split(
        self,
        chart_ratio: float = CHART_WIDTH_RATIO,
        gutter: int = Inches(0.25),
    ) -> tuple[ContentArea, ContentArea]:
        """切成左（圖表）右（文字）兩欄。"""
        chart_width = int((self.width - gutter) * chart_ratio)
        text_width = self.width - gutter - chart_width

        chart_area = ContentArea(self.left, self.top, chart_width, self.height)
        text_area = ContentArea(
            self.left + chart_width + gutter,
            self.top,
            text_width,
            self.height,
        )

        return chart_area, text_area


def _content_area(layout: Any) -> ContentArea:
    """從版面的 BODY placeholder 取得內容區範圍。"""
    for placeholder in layout.placeholders:
        if placeholder.placeholder_format.idx == PH_BODY:
            return ContentArea(
                left=placeholder.left,
                top=placeholder.top,
                width=placeholder.width,
                height=placeholder.height,
            )

    raise RenderError(
        f"版面 {layout.name!r} 找不到 BODY placeholder（idx={PH_BODY}），"
        "無法推導內容區範圍"
    )


def _find_layout(presentation: Presentation, name: str) -> Any:
    for layout in presentation.slide_layouts:
        if layout.name == name:
            return layout

    available = [layout.name for layout in presentation.slide_layouts]
    raise RenderError(f"模板中找不到版面 {name!r}。可用版面：{available}")


# ---------------------------------------------------------------------------
# 文字填充
# ---------------------------------------------------------------------------
def _fill_narrative(
    placeholder: Any,
    narrative: PageNarrative,
    store: MetricStore,
) -> list[str]:
    """
    把敘事填入文字框，佔位符在此代入實際數值。

    ``strict=False``：單一佔位符失敗不應讓整份簡報生不出來，
    改為保留原佔位符並回報錯誤，讓使用者一眼看出哪裡沒接上。
    """
    errors: list[str] = []
    text_frame = placeholder.text_frame
    text_frame.clear()
    text_frame.word_wrap = True

    headline, headline_errors = placeholders.render_text(
        narrative.headline, store, strict=False
    )
    errors.extend(headline_errors)

    first_paragraph = text_frame.paragraphs[0]
    first_paragraph.text = headline
    first_paragraph.level = 0

    for run in first_paragraph.runs:
        run.font.bold = True
        run.font.size = Pt(16)

    for bullet in narrative.bullets:
        rendered, bullet_errors = placeholders.render_text(
            bullet, store, strict=False
        )
        errors.extend(bullet_errors)

        paragraph = text_frame.add_paragraph()
        paragraph.text = rendered
        paragraph.level = 1

        for run in paragraph.runs:
            run.font.size = Pt(12)

    return errors


def _place_text_area(placeholder: Any, area: ContentArea) -> None:
    """把 BODY placeholder 移到指定欄位。"""
    placeholder.left = area.left
    placeholder.top = area.top
    placeholder.width = area.width
    placeholder.height = area.height


# ---------------------------------------------------------------------------
# 頁面組裝
# ---------------------------------------------------------------------------
def add_section_divider(presentation: Presentation, title: str) -> Any:
    """新增章節分隔頁。"""
    layout = _find_layout(presentation, SECTION_LAYOUT_NAME)
    slide = presentation.slides.add_slide(layout)

    if slide.shapes.title is not None:
        slide.shapes.title.text_frame.text = title

    return slide


def add_agenda_page(
    presentation: Presentation,
    chapters: Sequence[str],
    *,
    title: str = AGENDA_TITLE,
) -> Any:
    """
    新增目錄頁，逐條列出章節。

    目錄的內容完全來自章節清單，不另外撰寫——目錄與章節分隔頁不一致
    是簡報最容易出現、也最傷信任的瑕疵。
    """
    layout = _find_layout(presentation, CONTENT_LAYOUT_NAME)
    slide = presentation.slides.add_slide(layout)

    if slide.shapes.title is not None:
        slide.shapes.title.text_frame.text = title

    body = _body_placeholder(slide)

    if body is None:
        return slide

    text_frame = body.text_frame
    text_frame.clear()
    text_frame.word_wrap = True

    for index, chapter in enumerate(chapters):
        paragraph = (
            text_frame.paragraphs[0] if index == 0 else text_frame.add_paragraph()
        )
        paragraph.text = f"{index + 1:02d}　{chapter}"
        paragraph.level = 0

        for run in paragraph.runs:
            run.font.size = Pt(16)

    return slide


def add_closing_page(
    presentation: Presentation,
    message: str = CLOSING_MESSAGE,
) -> Any:
    """新增結尾頁（模板第 5 頁用的版面）。"""
    layout = _find_layout(presentation, CLOSING_LAYOUT_NAME)
    slide = presentation.slides.add_slide(layout)

    if slide.shapes.title is not None:
        slide.shapes.title.text_frame.text = message

    return slide


def add_content_page(
    presentation: Presentation,
    bundle: PageBundle,
    store: MetricStore,
) -> tuple[Any, list[str]]:
    """
    新增一頁內容頁：標題 + 原生圖表 + 敘事。

    Returns:
        (slide, 佔位符錯誤清單)。
    """
    layout = _find_layout(presentation, CONTENT_LAYOUT_NAME)
    slide = presentation.slides.add_slide(layout)
    errors: list[str] = []

    title_text = bundle.section.title or (
        bundle.chart.plan.chart_title if bundle.chart else ""
    )

    if slide.shapes.title is not None:
        slide.shapes.title.text_frame.text = title_text

    area = _content_area(layout)
    body = _body_placeholder(slide)

    if bundle.chart is None:
        # 無圖表的純文字頁，文字占滿內容區。
        if body is not None and bundle.narrative is not None:
            _place_text_area(body, area)
            errors.extend(_fill_narrative(body, bundle.narrative, store))

        return slide, errors

    chart_area, text_area = area.split()

    if body is not None:
        if bundle.narrative is None:
            # 沒有敘事就移除空的文字框，避免留下空白版面提示文字。
            body._element.getparent().remove(body._element)
        else:
            _place_text_area(body, text_area)
            errors.extend(_fill_narrative(body, bundle.narrative, store))
    elif bundle.narrative is not None:
        errors.append(
            f"第 {bundle.section.page_number} 頁找不到 BODY placeholder，"
            "敘事文字未能填入"
        )

    _insert_chart(slide, bundle.chart, chart_area)

    return slide, errors


def _body_placeholder(slide: Any) -> Any | None:
    for shape in slide.placeholders:
        if shape.placeholder_format.idx == PH_BODY:
            return shape

    return None


def _insert_chart(
    slide: Any,
    chart: ResolvedChart,
    area: ContentArea,
) -> Any:
    """
    透過 registry 插入原生圖表或原生表格。

    圖表走 ``add_chart()``——這是全系統唯一的圖表落地路徑，會同時寫入
    chart XML 快取與內嵌 workbook，兩者天生一致。表格走 ``add_table()``，
    產出的是真正的 PPT table 物件（FR-2.4：禁止文字方塊拼貼）。
    """
    skill = VISUAL_SKILLS.get(chart.skill_name)

    if skill is None:
        raise RenderError(
            f"圖表 skill {chart.skill_name!r} 未註冊，"
            f"可用選項：{sorted(VISUAL_SKILLS)}"
        )

    return skill(
        slide,
        chart.spec,
        left=Emu(area.left),
        top=Emu(area.top),
        width=Emu(area.width),
        height=Emu(area.height),
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def render_deck(
    bundles: Sequence[PageBundle],
    store: MetricStore,
    *,
    output_path: str | Path | None = None,
    template_path: str | Path | None = None,
    deck_title: str | None = None,
    keep_template_slides: bool = False,
    include_agenda: bool = True,
    include_closing: bool = True,
    closing_message: str = CLOSING_MESSAGE,
) -> RenderReport:
    """
    組裝整份簡報。

    Args:
        bundles: 每頁的素材。
        store: 唯一真相來源，用於代入敘事佔位符。
        output_path: 輸出 .pptx 路徑，預設 ``outputs/deck.pptx``。
        template_path: 模板路徑，預設 ``source/template.pptx``。
        deck_title: 有值時覆寫模板首頁（封面）標題。
        keep_template_slides: 是否保留模板原有的示範頁。預設 False，
            但模板頁的刪除需操作底層 XML，故目前僅保留首頁並記錄警告。
        include_agenda: 是否在封面後插入目錄頁。
        include_closing: 是否在最後插入結尾頁。
        closing_message: 結尾頁文字。

    版面順序：封面（模板首頁）→ 目錄 →（章節分隔頁 → 內容頁…）× N → 結尾頁。
    章節分隔頁依 ``bundle.section.chapter`` 變化自動插入；``chapter`` 為 None
    的頁面不產生分隔頁，可用來排非章節內容。

    Returns:
        :class:`RenderReport`。
    """
    template = Path(template_path or config.TEMPLATE_PPTX)

    if not template.exists():
        raise RenderError(f"找不到簡報模板：{template}")

    presentation = Presentation(str(template))
    report = RenderReport()

    if deck_title and presentation.slides:
        first_slide = presentation.slides[0]

        if first_slide.shapes.title is not None:
            first_slide.shapes.title.text_frame.text = deck_title

    if not keep_template_slides:
        removed = _remove_template_placeholder_slides(presentation)

        if removed:
            report.warnings.append(
                f"已移除模板中 {removed} 張示範頁（保留首頁）"
            )

    report.chapters = chapter_order(bundles)

    if include_agenda and report.chapters:
        add_agenda_page(presentation, report.chapters)

    current_chapter: str | None = None

    for bundle in bundles:
        chapter = bundle.section.chapter

        if chapter and chapter != current_chapter:
            add_section_divider(presentation, chapter)
            report.divider_count += 1
            current_chapter = chapter

        try:
            _, errors = add_content_page(presentation, bundle, store)
        except RenderError as error:
            report.warnings.append(
                f"第 {bundle.section.page_number} 頁組裝失敗：{error}"
            )
            continue

        report.page_count += 1

        if bundle.chart is not None:
            report.chart_count += 1

        if errors:
            report.placeholder_errors[bundle.section.page_number or 0] = errors

    if include_closing:
        add_closing_page(presentation, closing_message)

    report.slide_count = len(presentation.slides._sldIdLst)

    target = Path(output_path or (config.OUTPUT_DIR / "deck.pptx"))
    target.parent.mkdir(parents=True, exist_ok=True)
    presentation.save(str(target))

    report.output_path = target
    return report


def _remove_template_placeholder_slides(presentation: Presentation) -> int:
    """
    移除模板中除首頁外的示範頁。

    python-pptx 沒有高階刪頁 API，需同時從 sldIdLst 移除項目並
    drop 對應的 relationship，否則會留下孤立的 slide part。
    """
    slide_id_list = presentation.slides._sldIdLst
    entries = list(slide_id_list)

    if len(entries) <= 1:
        return 0

    removed = 0

    for entry in entries[1:]:
        presentation.part.drop_rel(entry.rId)
        slide_id_list.remove(entry)
        removed += 1

    return removed


def scatter_labels_pending(chart: ResolvedChart) -> bool:
    """
    散點圖是否有尚未渲染的資料點標籤。

    python-pptx 無高階 API 設定散點資料點標籤文字（需操作 ``c:dLbls``），
    此函式讓上層能明確得知這項限制，而非以為標籤已經畫上去。
    見設計文件 §8.1。
    """
    return isinstance(chart.spec, ScatterSpec) and bool(chart.spec.labels)
