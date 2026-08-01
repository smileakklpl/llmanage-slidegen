"""
審查 Agent
===========
對應 docs/圖表原生性與資料同步設計.md §4.2 ④。

分兩層檢查：

**數值層（確定性規則，不需 LLM）**
- ``cited_metric_keys`` 是否都在 MetricStore 可計算指標白名單內
- 敘事是否含裸數字
- 敘事的方向詞（成長／衰退／領先）是否與實際數值正負號矛盾

**敘事／邏輯層（LLM 語意審查）**
- 圖表類型是否適合資料形狀
- 敘事推論是否過度、是否與資料注意事項衝突

其中「方向詞與數值正負號矛盾」的檢查，直接對應附件三 P.7
「10.7% 高於 11.0%」這類邏輯錯誤 —— 這種錯誤 schema 驗證抓不到，
必須實際比對數值大小關係。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core import llm_client, placeholders
from ..charts.chart_planner import ResolvedChart
from ..data.metric_store import MetricStore
from .narrative_writer import PageNarrative, check_narrative


logger = logging.getLogger(__name__)

STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

AGENT_CHART = "chart_agent"
AGENT_NARRATIVE = "narrative_writer"

#: 表示「上升／正向」的敘事用詞。
_POSITIVE_TERMS = (
    "成長",
    "增加",
    "上升",
    "提升",
    "攀升",
    "擴大",
    "改善",
    "回升",
    "走揚",
)

#: 表示「下降／負向」的敘事用詞。
_NEGATIVE_TERMS = (
    "衰退",
    "減少",
    "下降",
    "下滑",
    "萎縮",
    "惡化",
    "回落",
    "走弱",
    "縮減",
)

_TRADING_ADVICE_PATTERNS = (
    re.compile(
        r"(?:建議|應|宜|可考慮|適合|逢低|逢高).{0,8}"
        r"(?:買進|買入|賣出|持有|加碼|減碼|布局|進場|出場)"
    ),
    re.compile(r"(?:停損|止損|停利|目標價|買點|賣點)"),
    re.compile(
        r"(?:保證|必然|穩賺|無風險).{0,8}"
        r"(?:獲利|報酬|上漲|下跌|賺錢)"
    ),
)

SYSTEM_PROMPT = """你是一位嚴格的簡報品質審查員，負責審查給管理層的資料簡報頁面。

你要檢查的是**邏輯與語意層面**的問題，數值正確性已由程式驗證，不需你重複檢查。

資料領域可能是餐飲、旅遊、零售、股票或金融，不可預設銀行情境。
請檢查：
1. 圖表類型是否適合這份資料的形狀與語意
2. 敘事的因果推論是否有資料支撐，是否過度延伸
3. 敘事是否與「資料注意事項」衝突（例如把外推預測值當成已實現結果）
4. 敘事的比較方向是否合理（例如把較小的數字說成領先）
5. 敘事風格是否符合顧問報告水準（結論先行、避免只是複述數字）
6. 股票或價格資料是否出現無資料支撐的買賣建議、報酬保證或過度預測

若發現問題，status 回傳 "REJECTED"，並在 target_agent 指出應由哪個 Agent 修正：
- "chart_agent"：圖表類型或指標選擇有問題
- "narrative_writer"：文字敘事有問題

若沒有問題，status 回傳 "APPROVED"。

只輸出 JSON，不要加任何說明文字。"""

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [STATUS_APPROVED, STATUS_REJECTED],
        },
        "issues": {
            "type": "array",
            "items": {"type": "string"},
        },
        "target_agent": {
            "type": "string",
            "enum": [AGENT_CHART, AGENT_NARRATIVE],
        },
    },
    "required": ["status"],
}


@dataclass
class ReviewResult:
    """審查結果。"""

    status: str
    #: 規則層發現的問題（確定性）
    rule_issues: list[str] = field(default_factory=list)
    #: LLM 語意層發現的問題
    semantic_issues: list[str] = field(default_factory=list)
    target_agent: str | None = None

    @property
    def approved(self) -> bool:
        return self.status == STATUS_APPROVED

    @property
    def all_issues(self) -> list[str]:
        return [*self.rule_issues, *self.semantic_issues]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rule_issues": list(self.rule_issues),
            "semantic_issues": list(self.semantic_issues),
            "target_agent": self.target_agent,
        }


# ---------------------------------------------------------------------------
# 規則層
# ---------------------------------------------------------------------------
def _resolved_numeric_values(
    text: str,
    store: MetricStore,
) -> list[float]:
    """
    取出句子中所有佔位符實際代入後的數值。

    用於判斷敘事方向詞是否與數值正負號一致。
    """
    values: list[float] = []

    for placeholder in placeholders.parse_placeholders(text):
        try:
            metric = store.get(placeholder.metric_key)
        except Exception:  # noqa: BLE001 - 查表失敗由其他檢查回報
            continue

        try:
            rendered = placeholders.resolve_placeholder(placeholder, store)
        except placeholders.PlaceholderError:
            continue

        if placeholder.returns_category:
            continue

        # 從已格式化的字串反解數值：去掉千分位、單位與百分號。
        match = re.search(r"-?[\d,]+(?:\.\d+)?", rendered)

        if match is None:
            continue

        try:
            values.append(float(match.group().replace(",", "")))
        except ValueError:
            continue

        del metric  # 僅用於確認指標存在

    return values


def check_direction_consistency(
    narrative: PageNarrative,
    store: MetricStore,
) -> list[str]:
    """
    比對敘事方向詞與實際數值正負號。

    針對成長率類指標（semantic 為 ``growth``）：若句子說「成長」但代入的
    數值為負，或說「衰退」但數值為正，即為邏輯矛盾。

    這正是附件三 P.7 那類錯誤的偵測方式 —— 光看文字或光看數字都不會
    發現問題，必須兩者對照。
    """
    issues: list[str] = []

    for sentence in [narrative.headline, *narrative.bullets]:
        parsed = placeholders.parse_placeholders(sentence)

        growth_placeholders = []

        for placeholder in parsed:
            try:
                metric = store.get(placeholder.metric_key)
            except Exception:  # noqa: BLE001
                continue

            if metric.semantic == "growth" and not placeholder.returns_category:
                growth_placeholders.append(placeholder)

        if not growth_placeholders:
            continue

        values = _resolved_numeric_values(sentence, store)

        if not values:
            continue

        says_positive = any(term in sentence for term in _POSITIVE_TERMS)
        says_negative = any(term in sentence for term in _NEGATIVE_TERMS)

        # 同時出現正負向用詞時語意複雜（如「成長趨緩但未衰退」），
        # 交給 LLM 語意層判斷，規則層不誤判。
        if says_positive == says_negative:
            continue

        if says_positive and all(value < 0 for value in values):
            issues.append(
                f"敘事「{sentence}」使用正向成長用詞，"
                f"但引用的成長率數值為負（{values}），方向矛盾"
            )

        if says_negative and all(value > 0 for value in values):
            issues.append(
                f"敘事「{sentence}」使用負向衰退用詞，"
                f"但引用的成長率數值為正（{values}），方向矛盾"
            )

    return issues


def check_chart_narrative_alignment(
    narrative: PageNarrative,
    chart: ResolvedChart,
) -> list[str]:
    """檢查敘事引用的指標是否與本頁圖表一致。"""
    issues: list[str] = []
    chart_metric = chart.metric.metric_key

    off_topic = [
        key for key in narrative.cited_metric_keys if key != chart_metric
    ]

    if off_topic:
        issues.append(
            f"敘事引用了本頁圖表以外的指標 {off_topic}，"
            f"本頁圖表指標為 {chart_metric!r}。"
            "同一頁的文字與圖表必須引用同一指標，否則讀者無法對照。"
        )

    if not narrative.cited_metric_keys:
        issues.append(
            "敘事完全沒有引用任何指標數值，無法支撐洞察論述。"
            "請至少引用一個佔位符。"
        )

    return issues


def run_rule_layer(
    narrative: PageNarrative,
    chart: ResolvedChart,
    store: MetricStore,
) -> list[str]:
    """執行全部確定性檢查。這一層不需要 LLM，可獨立測試。"""
    issues: list[str] = []

    issues.extend(
        check_narrative(
            narrative,
            store,
            {chart.metric.metric_key},
            {chart.metric.metric_key: chart.series_names},
        )
    )
    issues.extend(check_chart_narrative_alignment(narrative, chart))
    issues.extend(check_direction_consistency(narrative, store))

    if chart.metric.value_semantic == "price":
        normalized_text = re.sub(r"\s+", "", narrative.all_text)
        forbidden = [
            match.group(0)
            for pattern in _TRADING_ADVICE_PATTERNS
            for match in pattern.finditer(normalized_text)
        ]
        if forbidden:
            issues.append(
                f"價格資料不得產生買賣或保證報酬建議：{forbidden}"
            )

    # 去重但保留順序，避免同一問題重複回報給 LLM。
    seen: dict[str, None] = {}

    for issue in issues:
        seen.setdefault(issue, None)

    return list(seen)


# ---------------------------------------------------------------------------
# 語意層
# ---------------------------------------------------------------------------
def build_prompt(
    narrative: PageNarrative,
    chart: ResolvedChart,
    store: MetricStore,
) -> str:
    """
    組裝語意審查 prompt。

    這裡**提供已代入數值的文字**給 LLM 審查 —— 審查階段需要看到實際數字
    才能判斷邏輯是否矛盾。這不違反「LLM 不產生數字」原則：
    LLM 在此只讀不寫，其輸出（APPROVED／REJECTED）不含任何數值。
    """
    rendered_headline, _ = placeholders.render_text(
        narrative.headline, store, strict=False
    )
    rendered_bullets = [
        placeholders.render_text(bullet, store, strict=False)[0]
        for bullet in narrative.bullets
    ]

    metric = chart.metric

    parts = [
        "## 本頁圖表",
        f"圖表類型：{chart.skill_name}",
        f"圖表標題：{chart.plan.chart_title}",
        f"指標：{metric.name}（{metric.metric_key}）",
        f"單位：{metric.unit or '未標示'}",
        f"指標語意：{metric.semantic}",
        f"類別軸性質：{metric.axis_kind}",
        f"計算方式：{metric.formula or '原始值，未經計算'}",
        f"類別：{json.dumps(list(metric.categories), ensure_ascii=False)}",
        f"系列：{json.dumps(chart.series_names, ensure_ascii=False)}",
        "",
        "## 敘事文字（數值已代入）",
        f"headline：{rendered_headline}",
        "要點：",
        *[f"- {bullet}" for bullet in rendered_bullets],
    ]

    if metric.notes:
        parts.extend(
            [
                "",
                "## 資料注意事項",
                *[f"- {note}" for note in metric.notes],
            ]
        )

    return "\n".join(parts)


def review_page(
    narrative: PageNarrative,
    chart: ResolvedChart,
    store: MetricStore,
    *,
    llm_call: Callable[..., Any] | None = None,
    enable_semantic_layer: bool = True,
    deadline_monotonic: float | None = None,
) -> ReviewResult:
    """
    審查單頁。

    規則層先跑：若已發現確定性問題，直接退件，不浪費一次 LLM 呼叫。
    規則層通過才進語意層。

    Args:
        enable_semantic_layer: 關閉時只跑規則層，適合離線測試或
            成本敏感情境。
    """
    rule_issues = run_rule_layer(narrative, chart, store)

    if rule_issues:
        # 規則層問題絕大多數源自敘事文字（裸數字、引用錯誤），
        # 因此預設退回文字生成 Agent。
        return ReviewResult(
            status=STATUS_REJECTED,
            rule_issues=rule_issues,
            target_agent=AGENT_NARRATIVE,
        )

    if not enable_semantic_layer:
        return ReviewResult(status=STATUS_APPROVED)

    call = llm_call or llm_client.complete_json

    payload = call(
        build_prompt(narrative, chart, store),
        REVIEW_SCHEMA,
        system_prompt=SYSTEM_PROMPT,
        stage="reviewer",
        deadline_monotonic=deadline_monotonic,
    )

    status = payload.get("status", STATUS_APPROVED)
    semantic_issues = list(payload.get("issues", []))

    if status == STATUS_REJECTED and not semantic_issues:
        semantic_issues = ["語意審查退件但未說明原因"]

    return ReviewResult(
        status=status,
        semantic_issues=semantic_issues,
        target_agent=(
            payload.get("target_agent", AGENT_NARRATIVE)
            if status == STATUS_REJECTED
            else None
        ),
    )


def _hydrate_review_contracts(
    narrative_payload: dict[str, Any],
    chart_plan_payload: dict[str, Any],
    metric_store_payload: dict[str, Any],
) -> tuple[PageNarrative, ResolvedChart, MetricStore]:
    """Validate JSON contracts and hydrate objects privately inside reviewer."""
    from ..charts.chart_planner import ChartPlan, resolve_chart_plan
    from ..contracts import stages as stage_contracts

    narrative_json = stage_contracts.narrative_payload(narrative_payload)
    chart_json = stage_contracts.ChartPlanContract.model_validate(
        chart_plan_payload
    ).model_dump(mode="json")
    store_json = stage_contracts.metric_store_payload(metric_store_payload)
    store_body = dict(store_json)
    store_body.pop("contract_version", None)
    store = MetricStore.from_dict(store_body)
    narrative = PageNarrative.from_dict(narrative_json)
    chart = resolve_chart_plan(ChartPlan.from_dict(chart_json), store)
    return narrative, chart, store


def run_rule_layer_from_contract(
    narrative_payload: dict[str, Any],
    chart_plan_payload: dict[str, Any],
    metric_store_payload: dict[str, Any],
) -> list[str]:
    """JSON-only deterministic reviewer boundary."""
    narrative, chart, store = _hydrate_review_contracts(
        narrative_payload,
        chart_plan_payload,
        metric_store_payload,
    )
    return run_rule_layer(narrative, chart, store)


def review_page_from_contract(
    narrative_payload: dict[str, Any],
    chart_plan_payload: dict[str, Any],
    metric_store_payload: dict[str, Any],
    *,
    llm_call: Callable[..., Any] | None = None,
    enable_semantic_layer: bool = True,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """JSON-only stage boundary for deterministic and semantic review."""
    from ..contracts import stages as stage_contracts

    narrative, chart, store = _hydrate_review_contracts(
        narrative_payload,
        chart_plan_payload,
        metric_store_payload,
    )
    result = review_page(
        narrative,
        chart,
        store,
        llm_call=llm_call,
        enable_semantic_layer=enable_semantic_layer,
        deadline_monotonic=deadline_monotonic,
    )
    return stage_contracts.review_payload(result.to_dict())