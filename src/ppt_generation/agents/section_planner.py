"""
章節規劃 Agent
===============
對應 docs/圖表原生性與資料同步設計.md §4.2 ①。

職責：把使用者的一句話需求，轉成簡報的章節骨架。

關鍵規則：使用者未明確提供章節清單時，**必須**回傳
``NEEDS_CONFIRMATION`` 並附上待確認問題，由 Orchestrator 中斷流程等待
使用者輸入。Agent 不得自行假設章節結構就往下跑 —— 章節決定整份簡報的
敘事框架，猜錯的成本遠高於多問一次。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..core import llm_client
from ..data.metric_store import MetricStore


STATUS_READY = "READY"
STATUS_NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"

#: 內容頁數上限。規格書 FR-2.6 的預設輸出是 16 頁，含封面／目錄／章節頁／
#: 結尾頁等非內容頁，故內容頁上限訂在此。頁數可由使用者 prompt 覆寫。
MAX_SECTIONS = 16

#: FR-2.6 的預設章節骨架（來自附件二的系統提示詞）。
#: 使用者未指定章節時，這是「這份簡報長什麼樣」的預設答案；
#: 但仍**不會**繞過 NEEDS_CONFIRMATION——它是給 LLM 的參考骨架，
#: 不是替使用者作決定。
DEFAULT_CHAPTERS: tuple[str, ...] = (
    "Executive Summary",
    "市場整體概況",
    "同業成長及競爭分析",
    "客戶活躍度",
    "獲利能力",
    "風險與警訊",
    "未來趨勢推測",
    "對台新的策略建議",
)

#: 「未來趨勢推測」章節只能引用 forecast 類指標（FR-2.6），
#: writer 不得自行推測數值。這裡記下對應關係供 prompt 使用。
FORECAST_CHAPTER = "未來趨勢推測"

SYSTEM_PROMPT = """你是一位金融業管理顧問，負責規劃給銀行高階主管閱讀的簡報骨架。

任務：依使用者需求與可用指標目錄，規劃簡報的內容頁。

嚴格規則：
1. 你只能從提供的指標目錄（metric catalog）中挑選 metric_key，不得自行發明。
2. 你看不到任何實際數值，也不需要看到 —— 你的工作只是規劃結構。
3. 若使用者的需求沒有明確指出想看的章節或主題，status 必須回傳
   "NEEDS_CONFIRMATION"，並在 question_to_user 提出具體待確認問題。
4. 若使用者已明確說明章節或主題，status 回傳 "READY"。
5. 內容頁數量不得超過 {max_sections} 頁。
6. 每一頁都必須填 chapter，指出它屬於哪一個章節。同一章節的頁面要相鄰，
   不要交錯——章節在簡報中會各自產生一張章節分隔頁。
7. 未特別指定時，章節請採用預設骨架：{default_chapters}。
8. 「{forecast_chapter}」章節的頁面只能引用 forecast 類指標
   （metric_key 結尾為 .forecast）；若目錄中沒有這類指標，就不要規劃此章節。
9. 撰寫風格為商業洞察導向（類 McKinsey / BCG 顧問報告），而非數字整理。

只輸出 JSON，不要加任何說明文字。"""

SECTION_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [STATUS_READY, STATUS_NEEDS_CONFIRMATION],
        },
        "question_to_user": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "chapter": {
                        "type": "string",
                        "description": (
                            "此頁所屬章節名稱。同章節頁面需相鄰。"
                        ),
                    },
                    "intent": {"type": "string"},
                    "suggested_metric_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "title",
                    "chapter",
                    "intent",
                    "suggested_metric_keys",
                ],
            },
        },
    },
    "required": ["status", "sections"],
}


@dataclass
class SectionPlan:
    """一個章節的規劃結果。"""

    title: str
    intent: str
    suggested_metric_keys: list[str] = field(default_factory=list)
    #: 由 Orchestrator 指派的頁碼，非 LLM 決定
    page_number: int | None = None
    #: 所屬章節。renderer 依此插入章節分隔頁並產生目錄。
    #: None 代表不屬於任何章節（不會有分隔頁）。
    chapter: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "chapter": self.chapter,
            "intent": self.intent,
            "suggested_metric_keys": list(self.suggested_metric_keys),
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SectionPlan:
        chapter = payload.get("chapter")

        return cls(
            title=payload.get("title", ""),
            intent=payload.get("intent", ""),
            suggested_metric_keys=list(payload.get("suggested_metric_keys", [])),
            page_number=payload.get("page_number"),
            chapter=str(chapter).strip() if chapter else None,
        )


@dataclass
class SectionPlanResult:
    """章節規劃 Agent 的輸出契約。"""

    status: str
    sections: list[SectionPlan] = field(default_factory=list)
    question_to_user: str | None = None
    #: 被剔除的無效 metric_key 及原因，供除錯與回報
    dropped_metric_keys: dict[str, str] = field(default_factory=dict)

    @property
    def needs_confirmation(self) -> bool:
        return self.status == STATUS_NEEDS_CONFIRMATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sections": [section.to_dict() for section in self.sections],
            "question_to_user": self.question_to_user,
            "dropped_metric_keys": dict(self.dropped_metric_keys),
        }


def build_prompt(
    user_prompt: str,
    store: MetricStore,
    existing_sections: Sequence[str] | None = None,
) -> str:
    """組裝給 LLM 的 prompt。catalog 不含任何數值。"""
    parts = [
        "## 使用者需求",
        user_prompt.strip() or "（使用者未提供描述）",
        "",
        "## 可用指標目錄（僅 metadata，無實際數值）",
        store.catalog_as_prompt(),
    ]

    if existing_sections:
        parts.extend(
            [
                "",
                "## 使用者已指定的章節清單",
                json.dumps(list(existing_sections), ensure_ascii=False),
                "",
                "使用者已明確指定章節，status 應為 READY，"
                "請依此清單規劃並為每個章節挑選合適的 metric_key。",
            ]
        )

    blocked = store.blocked_metrics()

    if blocked:
        parts.extend(
            [
                "",
                "## 不可使用的指標（資料範圍不足，已被防呆擋下）",
                json.dumps(blocked, ensure_ascii=False, indent=2),
                "",
                "上述指標不得出現在 suggested_metric_keys 中。",
            ]
        )

    return "\n".join(parts)


def _sanitize_sections(
    raw_sections: Sequence[dict[str, Any]],
    store: MetricStore,
) -> tuple[list[SectionPlan], dict[str, str]]:
    """
    剔除 LLM 幻想出來的 metric_key。

    這是確定性防呆：即使 LLM 回傳了不存在或被擋下的指標，
    也不會流入下游的 ChartAgent。
    """
    allowed = set(store.computable_metric_keys())
    blocked = store.blocked_metrics()

    sections: list[SectionPlan] = []
    dropped: dict[str, str] = {}

    for raw in raw_sections[:MAX_SECTIONS]:
        section = SectionPlan.from_dict(raw)

        kept: list[str] = []

        for metric_key in section.suggested_metric_keys:
            if metric_key in allowed:
                kept.append(metric_key)
            elif metric_key in blocked:
                dropped[metric_key] = (
                    "指標被防呆擋下：" + "；".join(blocked[metric_key])
                )
            else:
                dropped[metric_key] = "指標不存在於 MetricStore"

        # FR-2.6：「未來趨勢推測」章節的數字一律引用 forecast 類指標。
        # 引用一般指標的話，這一頁會用實際值講「未來」——正是防呆要防的事。
        if section.chapter == FORECAST_CHAPTER:
            forecast_keys = [key for key in kept if key.endswith(".forecast")]

            for key in set(kept) - set(forecast_keys):
                dropped[key] = (
                    f"「{FORECAST_CHAPTER}」章節只能引用 forecast 類指標"
                )

            if not forecast_keys:
                # 沒有 forecast 指標可用時整頁不產出。留著空 metric_keys 會
                # 讓 ChartAgent 退回全部指標目錄，等於默默繞過這條規則。
                dropped[f"（章節）{section.title}"] = (
                    f"「{FORECAST_CHAPTER}」章節無可用的 forecast 指標，"
                    "整頁不產出"
                )
                continue

            kept = forecast_keys

        section.suggested_metric_keys = kept
        sections.append(section)

    sections = group_by_chapter(sections)

    for index, section in enumerate(sections, start=1):
        # 這裡先給一個從 1 起算的邏輯序號；實際簡報頁碼（含封面、目錄、
        # 章節分隔頁的偏移）由 renderer.assign_page_numbers 之後覆寫。
        section.page_number = index

    return sections, dropped


def group_by_chapter(sections: Sequence[SectionPlan]) -> list[SectionPlan]:
    """
    把同章節的頁面收攏成相鄰，章節順序依首次出現。

    章節在簡報裡會各自產生一張分隔頁。頁面交錯的話會出現
    「章節一 → 章節二 → 章節一」這種同一章節被切成兩段的簡報，
    所以不依賴 LLM 自己排好，這裡確定性地重組一次。
    """
    order: list[str | None] = []
    buckets: dict[str | None, list[SectionPlan]] = {}

    for section in sections:
        if section.chapter not in buckets:
            buckets[section.chapter] = []
            order.append(section.chapter)

        buckets[section.chapter].append(section)

    return [section for chapter in order for section in buckets[chapter]]


def plan_sections(
    user_prompt: str,
    store: MetricStore,
    *,
    existing_sections: Sequence[str] | None = None,
    llm_call: Callable[..., Any] | None = None,
) -> SectionPlanResult:
    """
    規劃簡報章節。

    Args:
        user_prompt: 使用者的自然語言需求。
        store: MetricStore，只用於提供 catalog 與驗證 metric_key。
        existing_sections: 使用者已指定的章節清單。有值時 LLM 應回 READY。
        llm_call: 覆寫 LLM 呼叫，測試時注入假回應。
            簽名同 :func:`llm_client.complete_json`。

    Returns:
        :class:`SectionPlanResult`。``needs_confirmation`` 為 True 時，
        Orchestrator 應中斷流程並把 ``question_to_user`` 回傳給使用者。
    """
    call = llm_call or llm_client.complete_json

    payload = call(
        build_prompt(user_prompt, store, existing_sections),
        SECTION_PLAN_SCHEMA,
        system_prompt=SYSTEM_PROMPT.format(
            max_sections=MAX_SECTIONS,
            default_chapters="、".join(DEFAULT_CHAPTERS),
            forecast_chapter=FORECAST_CHAPTER,
        ),
        stage="intent",
    )

    sections, dropped = _sanitize_sections(payload.get("sections", []), store)
    status = payload.get("status", STATUS_NEEDS_CONFIRMATION)

    # 防呆：LLM 說 READY 但沒給出任何章節時，仍視為需要確認，
    # 避免下游拿到空章節清單卻以為一切正常。
    if status == STATUS_READY and not sections:
        return SectionPlanResult(
            status=STATUS_NEEDS_CONFIRMATION,
            sections=[],
            question_to_user=(
                "無法從您的描述判斷簡報章節，請說明想呈現的主題或章節，"
                f"例如：市場整體概況、各業者競爭態勢。可用指標共 "
                f"{len(store.computable_metric_keys())} 項。"
            ),
            dropped_metric_keys=dropped,
        )

    return SectionPlanResult(
        status=status,
        sections=sections,
        question_to_user=payload.get("question_to_user"),
        dropped_metric_keys=dropped,
    )
