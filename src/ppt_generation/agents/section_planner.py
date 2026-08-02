"""
章節規劃 Agent
===============
對應 docs/圖表原生性與資料同步設計.md §4.2 ①。

職責：把使用者的一句話需求，轉成簡報的章節骨架。

關鍵行為：自然語言需求會先轉成結構化 IntentSpec，再規劃內容頁。若提示詞
模糊、措辭不佳、含錯字，或模型無法解析，系統不得因此停止；必須忽略無法套用
的部分、留下 warning，並以可計算指標建立確定性簡報。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from ..contracts.stages import IntentSpecContract
from ..core import config, llm_client
from ..data.metric_store import MetricStore


STATUS_READY = "READY"
STATUS_NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"

#: Product-level absolute ceiling. Deployments may lower this through
#: GENERATION_MAX_CONTENT_PAGES, but can never raise it above 15.
DEFAULT_CONTENT_PAGES = 8
MAX_SECTIONS = 15


def _content_page_limits() -> tuple[int, int]:
    """Return validated deployment defaults under the absolute product cap."""
    settings = config.load_generation_settings()
    return settings.default_content_pages, settings.max_content_pages


_CHINESE_PAGE_NUMBERS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "十七": 17,
    "十八": 18,
    "十九": 19,
    "二十": 20,
}
_ENGLISH_PAGE_NUMBERS = {
    name: value
    for value, name in enumerate(
        (
            "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen",
            "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
            "nineteen", "twenty",
        ),
        start=1,
    )
}
_PAGE_NUMBER_TOKEN = (
    r"\d+|二十|十[一二三四五六七八九]?|[一二三四五六七八九]|"
    + "|".join(_ENGLISH_PAGE_NUMBERS)
)
_PAGE_TARGET_PATTERN = re.compile(
    rf"(?P<count>{_PAGE_NUMBER_TOKEN})\s*"
    r"(?:個\s*)?(?:內容\s*)?(?:頁|張|pages?|slides?)",
    re.IGNORECASE,
)


def extract_target_content_pages(user_prompt: str) -> int | None:
    """Read an explicit page request; never trust an invented LLM count."""
    match = _PAGE_TARGET_PATTERN.search(user_prompt or "")
    if match is None:
        return None
    token = match.group("count").lower()
    if token.isdigit():
        return int(token)
    return _CHINESE_PAGE_NUMBERS.get(token) or _ENGLISH_PAGE_NUMBERS.get(token)


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
4. 解析使用者的目的、受眾、語言、語氣、內容頁數、主題、排除項、圖表偏好與
   結論要求，寫入 intent_spec。這些都是呈現需求，不得放入任何實際資料值。
5. 即使使用者描述模糊、帶情緒、措辭不佳、含錯字或提出無法由資料支持的要求，
   也不得拒絕或停止生成。忽略無法套用的部分、在 interpretation_warnings 說明，
   使用安全預設補足需求，並盡可能回傳 status="READY" 與至少一個合法內容頁。
6. 內容頁數量不得超過 {max_sections} 頁。若指定頁數超出範圍，使用合法上限。
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
        "intent_spec": {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "audience": {"type": "string"},
                "language": {"type": "string"},
                "tone": {"type": "string"},
                "target_content_pages": {"type": "integer"},
                "requested_topics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "excluded_topics": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "requested_metric_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "chart_preferences": {
                    "type": "object",
                    "properties": {
                        "preferred_types": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "avoided_types": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                },
                "required_conclusions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "delivery": {
                    "type": "object",
                    "properties": {
                        "recipients": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "email_subject": {"type": "string"},
                    },
                },
                "interpretation_warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
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
    intent_spec: dict[str, Any] = field(default_factory=dict)
    question_to_user: str | None = None
    #: 被剔除的無效 metric_key 及原因，供除錯與回報
    dropped_metric_keys: dict[str, str] = field(default_factory=dict)

    @property
    def needs_confirmation(self) -> bool:
        return self.status == STATUS_NEEDS_CONFIRMATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "intent_spec": dict(self.intent_spec),
            "sections": [section.to_dict() for section in self.sections],
            "question_to_user": self.question_to_user,
            "dropped_metric_keys": dict(self.dropped_metric_keys),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SectionPlanResult:
        return cls(
            status=payload.get("status", STATUS_NEEDS_CONFIRMATION),
            intent_spec=IntentSpecContract.model_validate(
                payload.get("intent_spec") or {}
            ).model_dump(mode="json"),
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


_ALLOWED_CHART_TYPES = {
    "line",
    "column",
    "bar",
    "pie",
    "scatter",
    "combo",
    "table",
    "heatmap",
}


def _string_list(value: Any) -> list[str]:
    """Normalize a permissive LLM field without rejecting the whole request."""
    if not isinstance(value, (list, tuple)):
        return []
    return list(
        dict.fromkeys(
            text for item in value if (text := str(item).strip())
        )
    )


def _sanitize_intent_spec(
    raw: Any,
    store: MetricStore,
    sections: Sequence[SectionPlan],
    *,
    source: str,
    extra_warnings: Sequence[str] = (),
) -> dict[str, Any]:
    """Build a safe IntentSpec; invalid prompt details become warnings, not errors."""
    payload = dict(raw) if isinstance(raw, dict) else {}
    warnings = [
        *_string_list(payload.get("interpretation_warnings")),
        *extra_warnings,
    ]
    allowed_metrics = set(store.computable_metric_keys())
    requested = _string_list(payload.get("requested_metric_keys"))
    unknown = [key for key in requested if key not in allowed_metrics]
    requested = [key for key in requested if key in allowed_metrics]

    if unknown:
        warnings.append(
            "已忽略資料無法支持的 requested_metric_keys：" + "、".join(unknown)
        )

    if not requested:
        requested = list(dict.fromkeys(
            key for section in sections for key in section.suggested_metric_keys
        ))

    default_pages, max_sections = _content_page_limits()
    target = default_pages
    raw_target = payload.get("target_content_pages")
    if raw_target is not None and not isinstance(raw_target, bool):
        try:
            parsed_target = int(raw_target)
        except (TypeError, ValueError):
            warnings.append(
                f"內容頁數無法辨識，已使用預設 {default_pages} 頁"
            )
        else:
            target = max(1, min(max_sections, parsed_target))
            if target != parsed_target:
                warnings.append(
                    f"內容頁數 {parsed_target} 超出範圍，已調整為 {target}"
                )

    chart_payload = payload.get("chart_preferences")
    chart_payload = chart_payload if isinstance(chart_payload, dict) else {}
    preferred_raw = [
        item.lower()
        for item in _string_list(chart_payload.get("preferred_types"))
    ]
    avoided_raw = [
        item.lower()
        for item in _string_list(chart_payload.get("avoided_types"))
    ]
    preferred = [item for item in preferred_raw if item in _ALLOWED_CHART_TYPES]
    avoided = [item for item in avoided_raw if item in _ALLOWED_CHART_TYPES]
    invalid_chart_types = [
        item
        for item in [*preferred_raw, *avoided_raw]
        if item not in _ALLOWED_CHART_TYPES
    ]
    if invalid_chart_types:
        warnings.append(
            "已忽略不支援的圖表偏好：" + "、".join(dict.fromkeys(invalid_chart_types))
        )
    preferred = [item for item in preferred if item not in set(avoided)]

    delivery = payload.get("delivery")
    delivery = delivery if isinstance(delivery, dict) else {}
    normalized = {
        "contract_version": "1.0",
        "objective": str(
            payload.get("objective")
            or "依可用資料產生可追蹤的管理決策簡報"
        ).strip(),
        "audience": str(payload.get("audience") or "管理層").strip(),
        "language": str(payload.get("language") or "zh-TW").strip(),
        "tone": str(payload.get("tone") or "professional").strip(),
        "target_content_pages": target,
        "requested_topics": _string_list(payload.get("requested_topics")),
        "excluded_topics": _string_list(payload.get("excluded_topics")),
        "requested_metric_keys": requested,
        "chart_preferences": {
            "preferred_types": preferred,
            "avoided_types": avoided,
        },
        "required_conclusions": _string_list(
            payload.get("required_conclusions")
        ),
        "delivery": {
            "recipients": _string_list(delivery.get("recipients")),
            "email_subject": (
                str(delivery.get("email_subject")).strip()
                if delivery.get("email_subject")
                else None
            ),
        },
        "interpretation_warnings": list(dict.fromkeys(warnings)),
        "prompt_applied": source == "parsed" and bool(payload or sections),
        "source": source,
    }
    return IntentSpecContract.model_validate(normalized).model_dump(mode="json")


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

    _, max_sections = _content_page_limits()
    for raw in raw_sections[:max_sections]:
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


def build_deterministic_sections(
    store: MetricStore,
    intent_spec: dict[str, Any] | None = None,
    *,
    fallback_reason: str | None = None,
) -> SectionPlanResult:
    """Build a safe plan even when the prompt is unusable or the LLM fails."""
    draft_intent = _sanitize_intent_spec(
        intent_spec,
        store,
        [],
        source="fallback",
        extra_warnings=([fallback_reason] if fallback_reason else []),
    )
    requested = [
        key
        for key in draft_intent["requested_metric_keys"]
        if key in store.computable_metric_keys()
    ]
    metric_keys = requested or store.computable_metric_keys()
    excluded = [item.casefold() for item in draft_intent["excluded_topics"]]
    if excluded:
        filtered = [
            key
            for key in metric_keys
            if not any(
                topic in store.get(key).name.casefold() for topic in excluded
            )
        ]
        if filtered:
            metric_keys = filtered
        else:
            draft_intent["interpretation_warnings"].append(
                "排除條件會移除所有可用指標，為確保仍可產出簡報，已忽略該條件"
            )

    page_limit = int(draft_intent["target_content_pages"])
    sections: list[SectionPlan] = []
    for metric_key in metric_keys[:page_limit]:
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

    draft_intent = _sanitize_intent_spec(
        draft_intent,
        store,
        sections,
        source="fallback",
    )
    return SectionPlanResult(
        status=STATUS_READY,
        sections=sections,
        intent_spec=draft_intent,
    )


def plan_sections(
    user_prompt: str,
    store: MetricStore,
    *,
    existing_sections: Sequence[str] | None = None,
    llm_call: Callable[..., Any] | None = None,
    deadline_monotonic: float | None = None,
) -> SectionPlanResult:
    """Parse requirements and plan pages without letting bad wording block output."""
    call = llm_call or llm_client.complete_json
    explicit_page_target = extract_target_content_pages(user_prompt)

    prompt = build_prompt(user_prompt, store, existing_sections)
    _, max_sections = _content_page_limits()
    system_prompt = SYSTEM_PROMPT.format(
        max_sections=max_sections,
        default_chapters="、".join(DEFAULT_CHAPTERS),
        forecast_chapter=FORECAST_CHAPTER,
        closing_chapter=DEFAULT_CHAPTERS[-1],
    )
    try:
        payload = call(
            prompt,
            SECTION_PLAN_SCHEMA,
            system_prompt=system_prompt,
            stage="intent",
            deadline_monotonic=deadline_monotonic,
        )
        if not isinstance(payload, dict):
            raise TypeError("intent LLM 回應不是 JSON object")
    except Exception as error:  # noqa: BLE001 - prompt must never block delivery
        return build_deterministic_sections(
            store,
            {"target_content_pages": explicit_page_target},
            fallback_reason=(
                "提示詞無法由模型解析，已使用可計算指標建立簡報："
                f"{type(error).__name__}"
            ),
        )

    raw_intent = dict(payload.get("intent_spec") or {})
    # Page count is a delivery control, so only an explicit number in the
    # original prompt may override the deployment default. The LLM cannot
    # invent a larger deck when the user did not ask for one.
    raw_intent["target_content_pages"] = explicit_page_target
    sections, dropped = _sanitize_sections(payload.get("sections", []), store)
    intent_spec = _sanitize_intent_spec(
        raw_intent,
        store,
        sections,
        source="parsed",
    )
    requested_keys = set(intent_spec["requested_metric_keys"])
    if requested_keys:
        scoped_sections: list[SectionPlan] = []
        for section in sections:
            kept = [
                key
                for key in section.suggested_metric_keys
                if key in requested_keys
            ]
            if not kept:
                continue
            section.suggested_metric_keys = kept
            section.suggested_series_by_metric = {
                key: section.suggested_series_by_metric[key]
                for key in kept
            }
            section.comparison_reason_by_metric = {
                key: section.comparison_reason_by_metric.get(key, "")
                for key in kept
            }
            scoped_sections.append(section)
        if len(scoped_sections) != len(sections):
            intent_spec["interpretation_warnings"].append(
                "已移除未包含在 requested_metric_keys 的內容頁"
            )
        sections = scoped_sections

    excluded_topics = [
        topic.casefold() for topic in intent_spec["excluded_topics"]
    ]
    if excluded_topics:
        filtered_sections = [
            section
            for section in sections
            if not any(
                topic
                in " ".join(
                    [section.title, section.chapter or "", section.intent]
                ).casefold()
                for topic in excluded_topics
            )
        ]
        if len(filtered_sections) != len(sections):
            intent_spec["interpretation_warnings"].append(
                "已依 excluded_topics 移除不需要的內容頁"
            )
        sections = filtered_sections

    target_pages = intent_spec.get("target_content_pages")
    if target_pages is not None and len(sections) > target_pages:
        sections = sections[:target_pages]
        intent_spec["interpretation_warnings"].append(
            f"內容頁已依需求限制為 {target_pages} 頁"
        )

    status = payload.get("status", STATUS_READY)
    if status != STATUS_READY or not sections:
        reason = (
            str(payload.get("question_to_user") or "").strip()
            or "提示詞未形成可用章節"
        )
        intent_spec["interpretation_warnings"].append(
            reason + "；已改用確定性章節規劃，簡報仍會產出"
        )
        return build_deterministic_sections(
            store,
            intent_spec,
            fallback_reason=reason,
        )

    intent_spec = IntentSpecContract.model_validate(intent_spec).model_dump(
        mode="json"
    )
    return SectionPlanResult(
        status=STATUS_READY,
        sections=sections,
        intent_spec=intent_spec,
        question_to_user=None,
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
    intent_spec_payload: dict[str, Any] | None = None,
    *,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """JSON-only fallback that preserves any valid parsed requirements."""
    from ..contracts import stages as stage_contracts

    validated_store = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(validated_store)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    return stage_contracts.section_stage_payload(
        build_deterministic_sections(
            store,
            intent_spec_payload,
            fallback_reason=fallback_reason,
        ).to_dict()
    )