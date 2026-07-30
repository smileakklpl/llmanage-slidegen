"""
文字生成 Agent
===============
對應 docs/圖表原生性與資料同步設計.md §4.2 ③。

職責：為每頁撰寫顧問風格的洞察敘事。

核心限制：**輸出中的數字只能以佔位符表示**。Agent 產出的是
``text_with_placeholders``，實際數值直到 renderer 階段才由
:mod:`placeholders` 從 MetricStore 查表代入。

這裡的規則層檢查（裸數字偵測、metric_key 白名單）在 Agent 內部就先跑一次，
違規即回饋給 LLM 重試，減少 ReviewerAgent 的退件次數。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import llm_client, placeholders
from ..charts.chart_planner import ResolvedChart
from ..data.metric_store import MetricStore
from .section_planner import SectionPlan


logger = logging.getLogger(__name__)

MAX_NARRATIVE_ATTEMPTS = 3

#: 每頁條列數上下限。顧問簡報一頁三到五個要點最易閱讀；
#: 下限存在的理由是實測發現模型很願意只寫一兩句就交差，
#: 那樣的頁面右半邊會空掉一大塊，看起來像沒做完。
MAX_BULLETS = 5
MIN_BULLETS = 3

#: 代入數值後的字數區間。長度以**代入後**的字數計算——佔位符本身很長
#: （``{{a.b|系列|latest}}`` 二十幾個字元），用原文計數會把一句短話誤判成長句。
MIN_HEADLINE_CHARS = 16
MAX_HEADLINE_CHARS = 60
MIN_BULLET_CHARS = 30
MAX_BULLET_CHARS = 120

#: 整頁敘事（headline + 要點）代入後的總字數下限。
#: 逐句都達標但整頁仍偏薄的情況存在，這條守住頁面的實際份量。
MIN_TOTAL_CHARS = 140

#: system prompt 分兩段拼接：前半段有 ``{}`` 需要代入上下限常數，
#: 後半段含 ``{{metric_key}}`` 佔位符語法，經過 ``format`` 會被吃掉一層大括號。
#: 分開處理比在字串裡寫 ``{{{{`` 好讀，也不會有人下次改動時踩到。
_STYLE_RULES = """你是一位資深金融業管理顧問，為銀行高階主管撰寫簡報洞察文字。

風格要求：
- 商業洞察導向（類 McKinsey / BCG / Deloitte 報告），不是數字整理
- 每個要點先講結論，再用數據支撐，最後點出「所以要注意什麼／該做什麼」
- 語氣專業精簡，不使用驚嘆號與誇飾

篇幅要求（會被程式檢查，不足會退回重寫）：
- headline：一句結論，代入數值後約 20-55 字，必須是主張而非描述
  好：「市場成長由簽帳金額驅動，發卡量已進入存量競爭」
  壞：「本頁呈現流通卡數與簽帳金額的月度趨勢」
- 要點：{min_bullets} 到 {max_bullets} 條，每條代入數值後 35-110 字，
  是完整的句子而不是標籤，寫出比較、幅度或原因，不要只重述圖表讀數
- 每條要點都要有實質資訊量：至少包含一個數據引用，或一個明確的判斷／建議
"""

_PLACEHOLDER_RULES = """
**最重要的規則：你絕對不可以寫出任何實際數字。**
所有數字必須以佔位符引用，格式為：

    {{metric_key|series_name|selector}}

- metric_key、series_name 必須完全來自提供的清單
- selector 可以是類別名稱（如 3月、中信），或以下關鍵字：
  latest（最後一期）、first（第一期）、max、min、sum、avg、
  max_category（最大值所在類別名稱）、min_category

正確範例：
  「市場流通卡數達 {{mkt.value|2026年|latest}}，年增 {{mkt.yoy|2026 vs 2025|latest}}」
錯誤範例（絕對禁止）：
  「市場流通卡數達 6,210 萬張，年增 5.3%」

年份、季度、Top N 這類結構性數字可以直接寫（如「2026 年」、「前 5 大」）。

只輸出 JSON，不要加任何說明文字。"""

SYSTEM_PROMPT = (
    _STYLE_RULES.format(min_bullets=MIN_BULLETS, max_bullets=MAX_BULLETS)
    + _PLACEHOLDER_RULES
)

NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "bullets": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["headline", "bullets"],
}


@dataclass
class PageNarrative:
    """
    一頁的敘事內容。

    ``headline`` 與 ``bullets`` 皆為含佔位符的文字，尚未代入數值。
    """

    page_number: int | None
    slide_title: str
    headline: str
    bullets: list[str] = field(default_factory=list)
    #: 文字中引用到的所有 metric_key（由程式解析，非 LLM 自述）
    cited_metric_keys: list[str] = field(default_factory=list)

    @property
    def all_text(self) -> str:
        """headline 與 bullets 合併，供規則檢查一次掃完。"""
        return "\n".join([self.headline, *self.bullets])

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "slide_title": self.slide_title,
            "headline": self.headline,
            "bullets": list(self.bullets),
            "cited_metric_keys": list(self.cited_metric_keys),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PageNarrative:
        return cls(
            page_number=payload.get("page_number"),
            slide_title=payload.get("slide_title", ""),
            headline=payload.get("headline", ""),
            bullets=list(payload.get("bullets", [])),
            cited_metric_keys=list(payload.get("cited_metric_keys", [])),
        )


@dataclass
class NarrativeResult:
    """文字生成 Agent 的輸出。"""

    narratives: list[PageNarrative] = field(default_factory=list)
    failures: dict[str, list[str]] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)


def build_prompt(
    section: SectionPlan,
    chart: ResolvedChart,
    store: MetricStore,
    previous_errors: list[str] | None = None,
) -> str:
    """
    組裝 prompt。

    只提供本頁圖表所引用的指標的佔位符說明（含類別與系列名稱，無數值），
    縮小 LLM 可引用的範圍，降低幻想 metric_key 的機會。
    """
    metric = chart.metric

    available = [
        {
            "metric_key": metric.metric_key,
            "name": metric.name,
            "unit": metric.unit,
            "semantic": metric.semantic,
            "axis_kind": metric.axis_kind,
            "series_names": chart.series_names,
            "categories": list(metric.categories),
            "formula": metric.formula,
        }
    ]

    parts = [
        "## 章節",
        f"標題：{section.title}",
        f"目的：{section.intent}",
        "",
        "## 本頁圖表",
        f"圖表類型：{chart.skill_name}",
        f"圖表標題：{chart.plan.chart_title}",
        "",
        "## 可引用的指標與佔位符（僅 metadata，無實際數值）",
        json.dumps(available, ensure_ascii=False, indent=2),
        "",
        (
            f"請撰寫 1 個 headline 與 {MIN_BULLETS}-{MAX_BULLETS} 個要點。"
            f"headline 代入數值後需 {MIN_HEADLINE_CHARS}-{MAX_HEADLINE_CHARS} 字，"
            f"每個要點需 {MIN_BULLET_CHARS}-{MAX_BULLET_CHARS} 字。"
            "字數不足會被程式退回重寫，請直接寫足。"
        ),
    ]

    label_hints = _label_hints(chart)

    if label_hints:
        parts.extend(["", "## 標籤讀法（寫錯會讓整句話語意不通）", *label_hints])

    if metric.notes:
        parts.extend(
            [
                "",
                "## 資料注意事項（撰寫時須考量，避免過度推論）",
                "\n".join(f"- {note}" for note in metric.notes),
            ]
        )

    if previous_errors:
        parts.extend(
            [
                "",
                "## 上一次的輸出違規，請修正後重寫",
                "\n".join(f"- {error}" for error in previous_errors),
            ]
        )

    return "\n".join(parts)


def _rendered_length(text: str, store: MetricStore) -> int:
    """
    計算代入數值後的字數。

    以代入後為準的理由：``{{cards.value|流通卡數|latest}}`` 有 30 個字元，
    但在簡報上只佔「6,049」五個字。用原文計字會讓一句「卡數 {{…}}」被
    當成長句放行，實際頁面上只有七個字。
    """
    rendered, _ = placeholders.render_text(text, store, strict=False)
    return len(rendered.strip())


def _check_length(
    narrative: PageNarrative,
    store: MetricStore,
) -> list[str]:
    """檢查敘事份量。長度是「文字夠不夠多」唯一能自動判斷的代理指標。"""
    issues: list[str] = []
    total = 0

    if narrative.headline.strip():
        length = _rendered_length(narrative.headline, store)
        total += length

        if length < MIN_HEADLINE_CHARS:
            issues.append(
                f"headline 代入數值後僅 {length} 字，"
                f"低於下限 {MIN_HEADLINE_CHARS} 字，請寫成完整的結論句"
            )
        elif length > MAX_HEADLINE_CHARS:
            issues.append(
                f"headline 代入數值後 {length} 字，"
                f"超過上限 {MAX_HEADLINE_CHARS} 字（重點訊息帶只有一行），"
                "請濃縮成一句"
            )

    for index, bullet in enumerate(narrative.bullets, start=1):
        length = _rendered_length(bullet, store)
        total += length

        if length < MIN_BULLET_CHARS:
            issues.append(
                f"第 {index} 個要點代入數值後僅 {length} 字，"
                f"低於下限 {MIN_BULLET_CHARS} 字，"
                "請補上比較基準、幅度或影響"
            )
        elif length > MAX_BULLET_CHARS:
            issues.append(
                f"第 {index} 個要點代入數值後 {length} 字，"
                f"超過上限 {MAX_BULLET_CHARS} 字，請拆句或精簡"
            )

    if total and total < MIN_TOTAL_CHARS:
        issues.append(
            f"整頁敘事代入數值後共 {total} 字，"
            f"低於下限 {MIN_TOTAL_CHARS} 字，頁面會顯得空洞"
        )

    return issues


def _roc_label_text(label: str) -> str | None:
    """把民國年月代碼轉成人看得懂的寫法：``11412`` → ``114 年 12 月``。"""
    text = str(label).strip()

    if not (text.isdigit() and len(text) == 5):
        return None

    month = int(text[3:])

    if not 1 <= month <= 12:
        return None

    return f"{int(text[:3])} 年 {month} 月"


def _label_hints(chart: ResolvedChart) -> list[str]:
    """
    提示 LLM 怎麼讀這一頁的標籤，避免寫出語意不通的句子。

    兩個實測踩到的坑：

    1. 金管會月報的期間欄名是民國年月代碼（``11412``）。直接照抄會在簡報上
       出現「規模在 11412 達到波段高峰」，讀者要自己翻譯。
    2. 「什麼時候達到高點」要用 ``max_category``（回傳類別名稱），
       用 ``max`` 會把數值代進「時間」的位置——實測出現過
       「市場規模於 60,485,911 達到波段高點」。
    """
    hints: list[str] = []
    labels = [*chart.metric.categories, *chart.series_names]
    samples = [
        (str(label), readable)
        for label in labels
        if (readable := _roc_label_text(label)) is not None
    ]

    if samples:
        listed = "、".join(
            f"{code} 指「{readable}」" for code, readable in samples[:3]
        )
        hints.append(
            f"- 期間標籤是民國年月代碼：{listed}。"
            "文字中請寫成「114 年 12 月」這種讀得懂的形式，不要照抄代碼。"
        )

    hints.append(
        "- 要講「何時」達到高／低點時，selector 用 max_category / min_category"
        "（回傳類別名稱）；用 max / min 會把數值代進時間的位置，"
        "產生「規模於 60,485,911 達到高點」這種句子。"
    )

    return hints


def check_narrative(
    narrative: PageNarrative,
    store: MetricStore,
    allowed_metric_keys: set[str] | None = None,
) -> list[str]:
    """
    規則層檢查（確定性，不呼叫 LLM）。

    檢查項目：
    1. 是否有裸數字（未經佔位符引用的數值）
    2. 引用的 metric_key 是否都在允許清單內
    3. 每個佔位符是否都能實際查表成功

    Returns:
        違規訊息清單。空清單代表通過。
    """
    issues: list[str] = []
    text = narrative.all_text

    if not narrative.headline.strip():
        issues.append("headline 不可為空")

    if not narrative.bullets:
        issues.append("bullets 不可為空")

    if len(narrative.bullets) > MAX_BULLETS:
        issues.append(
            f"要點數 {len(narrative.bullets)} 超過上限 {MAX_BULLETS}"
        )

    if narrative.bullets and len(narrative.bullets) < MIN_BULLETS:
        issues.append(
            f"要點數 {len(narrative.bullets)} 少於下限 {MIN_BULLETS}，"
            "請補足到足以支撐一頁的資訊量"
        )

    issues.extend(_check_length(narrative, store))

    bare = placeholders.find_bare_numbers(text)

    if bare:
        issues.append(
            f"文字中出現未經佔位符引用的數字 {bare}。"
            "所有指標數值必須寫成 {{metric_key|series_name|selector}} 形式。"
        )

    allowed = allowed_metric_keys or set(store.computable_metric_keys())
    cited = placeholders.cited_metric_keys(text)

    if not cited:
        issues.append(
            "整頁沒有引用任何指標佔位符。敘事必須以 "
            "{{metric_key|series_name|selector}} 引用本頁指標，"
            "否則這一頁的文字與圖表沒有任何可驗證的連結。"
        )

    unknown = [key for key in cited if key not in allowed]

    if unknown:
        issues.append(
            f"引用了不允許的 metric_key {unknown}，"
            f"本頁可用指標：{sorted(allowed)}"
        )

    # 逐一嘗試代入，抓出系列名稱或 selector 寫錯的情況。
    _, render_errors = placeholders.render_text(text, store, strict=False)
    issues.extend(render_errors)

    return issues


def write_narrative_for_page(
    section: SectionPlan,
    chart: ResolvedChart,
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_NARRATIVE_ATTEMPTS,
) -> tuple[PageNarrative | None, list[str], int]:
    """
    為單頁撰寫敘事，內含規則層自我校正迴圈。

    Returns:
        (PageNarrative 或 None, 最後一輪的違規訊息, 實際嘗試次數)。
    """
    call = llm_call or llm_client.complete_json
    allowed = {chart.metric.metric_key}
    issues: list[str] = []

    for attempt in range(1, max_attempts + 1):
        payload = call(
            build_prompt(section, chart, store, issues),
            NARRATIVE_SCHEMA,
            system_prompt=SYSTEM_PROMPT,
            stage="writer",
        )

        narrative = PageNarrative(
            page_number=section.page_number,
            slide_title=section.title,
            headline=payload.get("headline", ""),
            bullets=list(payload.get("bullets", [])),
        )

        # cited_metric_keys 由程式解析，不採信 LLM 自述。
        narrative.cited_metric_keys = placeholders.cited_metric_keys(
            narrative.all_text
        )

        issues = check_narrative(narrative, store, allowed)

        if not issues:
            return narrative, [], attempt

        logger.info(
            "頁面 %r 第 %d 次敘事未通過規則檢查：%s",
            section.title,
            attempt,
            issues,
        )

    return None, issues, max_attempts


def write_narratives(
    pairs: list[tuple[SectionPlan, ResolvedChart]],
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    max_attempts: int = MAX_NARRATIVE_ATTEMPTS,
) -> NarrativeResult:
    """
    為多頁撰寫敘事。

    註：實際部署時這裡應依 ``LLM_MAX_PARALLEL`` 平行化
    （敘事是整條管線最耗時的部分），目前先以序列實作確保正確性。
    """
    result = NarrativeResult()

    for section, chart in pairs:
        narrative, issues, attempts = write_narrative_for_page(
            section,
            chart,
            store,
            llm_call=llm_call,
            max_attempts=max_attempts,
        )

        result.attempts[section.title] = attempts

        if narrative is None:
            result.failures[section.title] = issues or ["未知原因"]
            continue

        result.narratives.append(narrative)

    return result
