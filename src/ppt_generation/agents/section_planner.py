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
    "核心概況",
    "趨勢與變化",
    "分群與排行",
    "異常、風險與限制",
    "預測與情境",
    "建議與下一步",
)

#: 預測章節只能引用 deterministic engine 已核准的 forecast 指標。
FORECAST_CHAPTER = "預測與情境"

#: 結論章節名稱。renderer 會在所有內容頁之後加一張結論頁，把每個章節的
#: 結論句收攏成一頁；這裡定義它的標題，讓目錄與頁面標題共用同一個字串。
#:
#: 為什麼不放進 DEFAULT_CHAPTERS：那組章節是 FR-2.6 明訂的骨架（附件二），
#: 動它等於改規格。結論是**簡報結構**的一部分，不是資料章節——它不引用
#: 新指標，只回收既有頁面的結論，所以由 renderer 確定性產生。
CONCLUSION_CHAPTER = "結論與後續行動"

SYSTEM_PROMPT = """你是一位資料分析與管理顧問，負責規劃給管理層閱讀的簡報骨架。

任務：依使用者需求與可用指標目錄，規劃簡報的內容頁。

嚴格規則：
1. 你只能從提供的指標目錄（metric catalog）中挑選 metric_key 與 series_names，
   不得自行發明。
2. 你看不到任何實際數值，也不需要看到 —— 你的工作只是規劃結構。
3. 每一頁必須用 metric_scopes 明確列出 metric_key 與該頁允許的 series_names。
   series_names 只能選與頁面 title / intent 直接相關的資料；禁止因為同一個
   metric_key 還有其他系列，就把規模、數量、金額等不同主題全部放進同一頁。
   單一主題頁通常只選一個系列。只有 intent 明確要求多期比較或兩項指標關係時，
   才能選多個系列，且必須在 comparison_reason 寫出比較理由；單一系列時填空字串。
   不可用空陣列表示「全部」。
4. 若使用者的需求沒有明確指出想看的章節或主題，status 必須回傳
   "NEEDS_CONFIRMATION"，並在 question_to_user 提出具體待確認問題。
5. 若使用者已明確說明章節或主題，status 回傳 "READY"。
6. 內容頁數量不得超過 {max_sections} 頁。
7. 每一頁都必須填 chapter，指出它屬於哪一個章節。同一章節的頁面要相鄰，
   不要交錯——章節在簡報中會各自產生一張章節分隔頁。
8. 未特別指定時，章節請採用預設骨架：{default_chapters}。
9. 「{forecast_chapter}」章節的頁面只能引用 forecast 類指標
   （metric_key 結尾為 .forecast）；若目錄中沒有這類指標，就不要規劃此章節。
10. 撰寫風格為商業洞察導向（類 McKinsey / BCG 顧問報告），而非數字整理。
11. **最後一個章節必須是收斂性的結論章節**（預設骨架中的
    「{closing_chapter}」即扮演此角色）：它不再引入新主題，而是把前面各章
    的發現收成「所以我們該做什麼」。這一章的 intent 要寫明它要回答的
    決策問題，不要只寫「呈現某指標」。
12. 每一頁的 intent 都要寫成一個問句或一句結論主張（例如「需求成長主要來自
    訂單量而非平均單價」），不要寫成「展示 X 指標的趨勢」——intent 是下游
    撰寫敘事的依據，寫成資料描述會得到資料描述的文案。
13. 領域可能是餐飲、旅遊、零售、股票或金融；只能依使用者需求與 catalog
    判斷語境，不可預設銀行、市場競爭或客戶經營。

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
                    "metric_scopes": {
                        "type": "array",
                        "minItems": 1,
                        "description": (
                            "本頁允許使用的指標與系列白名單；每個 series_names "
                            "都必須與 title/intent 直接相關。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "metric_key": {"type": "string"},
                                "series_names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "minItems": 1,
                                },
                                "comparison_reason": {
                                    "type": "string",
                                    "description": (
                                        "單一系列填空字串；選兩個以上系列時必須說明"
                                        "為何 title/intent 明確要求比較這些系列。"
                                    ),
                                },
                            },
                            "required": [
                                "metric_key",
                                "series_names",
                                "comparison_reason",
                            ],
                        },
                    },
                },
                "required": [
                    "title",
                    "chapter",
                    "intent",
                    "metric_scopes",
                ],
            },
        },
    },
    # 只有 status 是必填。``sections`` 刻意不列入必填：模型回
    # NEEDS_CONFIRMATION 時本來就沒有章節可給，強制要求它同時附一個空陣列
    # 會讓每次「需要確認」都變成 schema 驗證失敗、重試三次後整條管線中斷
    # （實測 gemini-3.6-flash 就是這樣掛掉的）。缺欄位由下游視為空清單，
    # 而 READY 但無章節的情況已有另一道防呆會轉回 NEEDS_CONFIRMATION。
    "required": ["status"],
}


@dataclass
class SectionPlan:
    """一個章節的規劃結果。"""

    title: str
    intent: str
    suggested_metric_keys: list[str] = field(default_factory=list)
    #: 每個 metric_key 在本頁允許使用的 series 白名單。
    #: 下游 chart agent 必須把 LLM 選擇限制在這個範圍內。
    suggested_series_by_metric: dict[str, list[str]] = field(default_factory=dict)
    #: 多系列 scope 的明確比較理由。沒有理由時只允許單一 series；
    #: 這讓下游 validator 不必猜測「多系列」究竟是有意比較或模型誤全選。
    comparison_reason_by_metric: dict[str, str] = field(default_factory=dict)
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
            "metric_scopes": [
                {
                    "metric_key": metric_key,
                    "series_names": list(
                        self.suggested_series_by_metric.get(metric_key, [])
                    ),
                    "comparison_reason": self.comparison_reason_by_metric.get(
                        metric_key, ""
                    ),
                }
                for metric_key in self.suggested_metric_keys
            ],
            "page_number": self.page_number,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SectionPlan:
        chapter = payload.get("chapter")
        scopes = payload.get("metric_scopes") or []
        series_by_metric = {
            str(scope.get("metric_key", "")): [
                str(name) for name in scope.get("series_names", []) if str(name)
            ]
            for scope in scopes
            if isinstance(scope, dict) and str(scope.get("metric_key", ""))
        }
        comparison_reasons = {
            str(scope.get("metric_key", "")): str(
                scope.get("comparison_reason", "")
            ).strip()
            for scope in scopes
            if isinstance(scope, dict) and str(scope.get("metric_key", ""))
        }
        metric_keys = list(series_by_metric) or list(
            payload.get("suggested_metric_keys", [])
        )

        return cls(
            title=payload.get("title", ""),
            intent=payload.get("intent", ""),
            suggested_metric_keys=metric_keys,
            suggested_series_by_metric=series_by_metric,
            comparison_reason_by_metric=comparison_reasons,
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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SectionPlanResult:
        return cls(
            status=payload.get("status", STATUS_NEEDS_CONFIRMATION),
            sections=[
                SectionPlan.from_dict(item)
                for item in payload.get("sections", [])
            ],
            question_to_user=payload.get("question_to_user"),
            dropped_metric_keys=dict(
                payload.get("dropped_metric_keys", {})
            ),
        )


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
                "請依此清單規劃，並為每頁以 metric_scopes 挑選合適的 "
                "metric_key 與直接相關的 series_names。",
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
                "上述指標不得出現在 metric_scopes 中。",
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
        kept_series: dict[str, list[str]] = {}
        kept_reasons: dict[str, str] = {}

        for metric_key in section.suggested_metric_keys:
            if metric_key in blocked:
                dropped[metric_key] = (
                    "指標被防呆擋下：" + "；".join(blocked[metric_key])
                )
                continue

            if metric_key not in allowed:
                dropped[metric_key] = "指標不存在於 MetricStore"
                continue

            metric = store.get(metric_key)
            requested = section.suggested_series_by_metric.get(metric_key, [])

            # 單系列 metric 沒有選錯空間，可安全補上；多系列 metric 必須由
            # section planner 明確限縮，空陣列不得再解讀為「全部」。
            if not requested and len(metric.series_names) == 1:
                requested = list(metric.series_names)

            if not requested:
                dropped[f"{section.title}:{metric_key}"] = (
                    "多系列指標必須在 metric_scopes 明確指定與頁面主題相關的 "
                    "series_names，不可留空代表全部"
                )
                continue

            unknown = [
                name for name in requested if name not in metric.series
            ]
            if unknown:
                dropped[f"{section.title}:{metric_key}"] = (
                    f"系列不存在：{unknown}；可用系列：{metric.series_names}"
                )

            valid = list(dict.fromkeys(
                name for name in requested if name in metric.series
            ))
            if not valid:
                continue

            comparison_reason = section.comparison_reason_by_metric.get(
                metric_key, ""
            ).strip()
            if len(valid) > 1 and not comparison_reason:
                dropped[f"{section.title}:{metric_key}"] = (
                    "同一頁選取多個系列時必須提供 comparison_reason，"
                    "明確說明 title/intent 要比較的關係；否則 fail-closed"
                )
                continue

            kept.append(metric_key)
            kept_series[metric_key] = valid
            if comparison_reason:
                kept_reasons[metric_key] = comparison_reason

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
            kept_series = {
                key: kept_series[key] for key in forecast_keys
            }

            kept_reasons = {
                key: kept_reasons[key]
                for key in forecast_keys
                if key in kept_reasons
            }

        if not kept:
            dropped[f"（章節）{section.title}"] = (
                "本頁所有 metric scope 均未通過確定性清洗，整頁不產出"
            )
            continue

        section.suggested_metric_keys = kept
        section.suggested_series_by_metric = kept_series
        section.comparison_reason_by_metric = kept_reasons
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


def build_deterministic_sections(store: MetricStore) -> SectionPlanResult:
    """Build a metadata-only section plan when the intent LLM is unavailable."""
    sections: list[SectionPlan] = []

    for metric_key in store.computable_metric_keys()[:MAX_SECTIONS]:
        metric = store.get(metric_key)
        if not metric.series_names:
            continue

        series_name = metric.series_names[-1]
        chapter = (
            "趨勢與變化"
            if metric.axis_kind == "temporal"
            else "分群與比較"
        )
        sections.append(
            SectionPlan(
                title=metric.name,
                chapter=chapter,
                intent=f"以可追溯資料說明{metric.name}的變化、管理意涵與下一步",
                suggested_metric_keys=[metric_key],
                suggested_series_by_metric={metric_key: [series_name]},
                comparison_reason_by_metric={metric_key: ""},
            )
        )

    if not sections:
        raise ValueError("MetricStore 沒有可建立簡報頁面的可計算指標")

    return SectionPlanResult(status=STATUS_READY, sections=sections)


def plan_sections(
    user_prompt: str,
    store: MetricStore,
    *,
    existing_sections: Sequence[str] | None = None,
    llm_call: Callable[..., Any] | None = None,
    deadline_monotonic: float | None = None,
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
            closing_chapter=DEFAULT_CHAPTERS[-1],
        ),
        stage="intent",
        deadline_monotonic=deadline_monotonic,
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
                f"例如：核心概況、趨勢變化、分群排行或決策建議。可用指標共 "
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


def plan_sections_from_contract(
    user_prompt: str,
    metric_store_payload: dict[str, Any],
    *,
    existing_sections: Sequence[str] | None = None,
    llm_call: Callable[..., Any] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """JSON-only stage boundary for section planning."""
    from ..contracts import stages as stage_contracts

    validated_store = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(validated_store)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    result = plan_sections(
        user_prompt,
        store,
        existing_sections=existing_sections,
        llm_call=llm_call,
        deadline_monotonic=deadline_monotonic,
    )
    return stage_contracts.section_stage_payload(result.to_dict())


def build_deterministic_sections_from_contract(
    metric_store_payload: dict[str, Any],
) -> dict[str, Any]:
    """JSON-only deterministic fallback for section planning."""
    from ..contracts import stages as stage_contracts

    validated_store = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(validated_store)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    return stage_contracts.section_stage_payload(
        build_deterministic_sections(store).to_dict()
    )