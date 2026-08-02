"""Versioned JSON contracts between generation stages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRefContract(BaseModel):
    filename: str
    sheet_name: str | None = None
    cell: str | None = None
    cell_range: str | None = None
    page_number: int | None = None
    extraction_method: str = "unknown"
    confidence: float = 1.0


class MetricSeriesContract(BaseModel):
    metric_key: str
    name: str
    categories: list[str]
    series: dict[str, list[float | None]]
    unit: str | None = None
    series_units: dict[str, str | None] = Field(default_factory=dict)
    semantic: str = "value"
    value_semantic: str = "unknown"
    aggregation_semantic: str = "none"
    allowed_derivations: list[str] = Field(default_factory=list)
    shape_kind: str = "unknown"
    axis_kind: str = "categorical"
    computable: bool = True
    notes: list[str] = Field(default_factory=list)
    evidence: dict[str, SourceRefContract] = Field(default_factory=dict)
    formula: str | None = None
    requires_human_review: bool = False


class MetricStoreContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    source_files: list[str] = Field(default_factory=list)
    metrics: list[MetricSeriesContract]


class MetricEngineReportContract(BaseModel):
    metric_count: int = Field(ge=0)
    blocked: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    dataset_profiles: list[dict[str, Any]] = Field(default_factory=list)


class MetricEngineResultContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    dataset_ids: list[str]
    engine_report: MetricEngineReportContract
    metric_store: MetricStoreContract


class PipelineRequestContract(BaseModel):
    """Versioned JSON invocation boundary from core into ppt_generation."""

    contract_version: Literal["1.0"] = "1.0"
    ingestion_payload: dict[str, Any]
    ingestion_source: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    sections: list[str] | None = None
    output_dir: str = Field(min_length=1)
    use_fake_llm: bool = False
    skip_semantic_review: bool = False
    stop_after: Literal[
        "metrics",
        "sections",
        "charts",
        "narratives",
        "review",
        "render",
        "verify",
    ] = "verify"
    dump_dir: str | None = None
    deck_title: str | None = None
    revision_intent: RevisionIntentContract | None = None
    generation_policy: Literal["strict", "required"]
    generation_deadline_seconds: float = Field(gt=0)
    generation_render_reserve_seconds: float = Field(ge=0)
    generation_llm_budget_exhausted: bool = False
    source_objects: list[dict[str, Any]] = Field(default_factory=list)
    template_path: str | None = None

    @model_validator(mode="after")
    def validate_deadline_reserve(self) -> PipelineRequestContract:
        if (
            self.generation_render_reserve_seconds
            >= self.generation_deadline_seconds
        ):
            raise ValueError(
                "generation_render_reserve_seconds 必須小於 deadline"
            )
        return self


class PageRevisionIntentContract(BaseModel):
    """Page-scoped presentation intent; business values are forbidden."""

    model_config = ConfigDict(extra="forbid")

    target_page_number: int = Field(ge=1)
    target_page_title: str = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=16000)
    preferred_chart_type: Literal[
        "bar", "column", "line", "pie", "scatter", "combo", "table", "heatmap"
    ] | None = None


class RevisionIntentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    base_run_name: str = Field(min_length=1)
    page_revisions: list[PageRevisionIntentContract] = Field(min_length=1)
    preserve_unmentioned_pages: Literal[True] = True


class ChartPreferencesContract(BaseModel):
    """Presentation choices parsed from the prompt; never contains data values."""

    preferred_types: list[str] = Field(default_factory=list)
    avoided_types: list[str] = Field(default_factory=list)


class DeliveryIntentContract(BaseModel):
    """Delivery metadata only; backend decides whether and how to send mail."""

    recipients: list[str] = Field(default_factory=list)
    email_subject: str | None = None


class IntentSpecContract(BaseModel):
    """Validated interpretation of the user's natural-language requirements."""

    contract_version: Literal["1.0"] = "1.0"
    objective: str = "依可用資料產生可追蹤的管理決策簡報"
    audience: str = "管理層"
    language: str = "zh-TW"
    tone: str = "professional"
    target_content_pages: int | None = Field(default=None, ge=1, le=15)
    requested_topics: list[str] = Field(default_factory=list)
    excluded_topics: list[str] = Field(default_factory=list)
    requested_metric_keys: list[str] = Field(default_factory=list)
    chart_preferences: ChartPreferencesContract = Field(
        default_factory=ChartPreferencesContract
    )
    required_conclusions: list[str] = Field(default_factory=list)
    page_revisions: list[PageRevisionIntentContract] = Field(default_factory=list)
    delivery: DeliveryIntentContract = Field(
        default_factory=DeliveryIntentContract
    )
    interpretation_warnings: list[str] = Field(default_factory=list)
    prompt_applied: bool = False
    source: Literal["parsed", "defaulted", "fallback"] = "defaulted"


class MetricScopeContract(BaseModel):
    metric_key: str
    series_names: list[str] = Field(min_length=1)
    comparison_reason: str = ""


class SectionContract(BaseModel):
    title: str
    chapter: str | None = None
    intent: str
    metric_scopes: list[MetricScopeContract] = Field(min_length=1)
    page_number: int | None = None


class SectionStageContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    status: Literal["READY", "NEEDS_CONFIRMATION"]
    intent_spec: IntentSpecContract = Field(default_factory=IntentSpecContract)
    sections: list[SectionContract] = Field(default_factory=list)
    question_to_user: str | None = None
    dropped_metric_keys: dict[str, str] = Field(default_factory=dict)
    delivery_warning: str | None = None


class ChartPlanContract(BaseModel):
    slide_title: str
    chart_type: str
    chart_title: str
    metric_key: str
    series_names: list[str] | None = None
    page_number: int | None = None


class ChartStageContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    plans: list[ChartPlanContract]
    failures: dict[str, list[str]] = Field(default_factory=dict)
    attempts: dict[str, int] = Field(default_factory=dict)
    delivery_warnings: dict[str, str] = Field(default_factory=dict)


class PageNarrativeContract(BaseModel):
    page_number: int | None = None
    slide_title: str
    headline: str
    bullets: list[str]
    cited_metric_keys: list[str] = Field(default_factory=list)


class NarrativeAttemptContract(BaseModel):
    narrative: PageNarrativeContract | None = None
    issues: list[str] = Field(default_factory=list)
    attempts: int = Field(ge=0)


class ReviewContract(BaseModel):
    page_number: int | None = None
    section_title: str = ""
    status: str
    rule_issues: list[str] = Field(default_factory=list)
    semantic_issues: list[str] = Field(default_factory=list)
    target_agent: str | None = None
    delivery_status: str | None = None
    hard_rules_passed: bool | None = None
    selected_candidate_hard_issues: list[str] = Field(default_factory=list)
    candidate_source: str | None = None
    warning: str | None = None
    delivery_warning: str | None = None
    writer_attempts: int | None = None
    reviewer_attempts: int | None = None


class DeckPageContract(BaseModel):
    section: SectionContract
    chart_plan: ChartPlanContract
    narrative: PageNarrativeContract
    review: ReviewContract | None = None


class DeckSpecContract(BaseModel):
    contract_version: Literal["1.0"] = "1.0"
    title: str
    intent_spec: IntentSpecContract = Field(default_factory=IntentSpecContract)
    metric_store: MetricStoreContract
    pages: list[DeckPageContract] = Field(min_length=1)
    generation_policy: Literal["strict", "required"]
    delivery_warnings: list[str] = Field(default_factory=list)


def metric_store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {"contract_version": "1.0", **payload}
    return MetricStoreContract.model_validate(envelope).model_dump(mode="json")


def metric_engine_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {"contract_version": "1.0", **payload}
    return MetricEngineResultContract.model_validate(envelope).model_dump(
        mode="json"
    )


def section_stage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {"contract_version": "1.0", **payload}
    return SectionStageContract.model_validate(envelope).model_dump(mode="json")


def chart_stage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = {"contract_version": "1.0", **payload}
    return ChartStageContract.model_validate(envelope).model_dump(mode="json")


def narrative_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return PageNarrativeContract.model_validate(payload).model_dump(mode="json")


def narrative_attempt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return NarrativeAttemptContract.model_validate(payload).model_dump(mode="json")


def review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return ReviewContract.model_validate(payload).model_dump(mode="json")


def deck_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return DeckSpecContract.model_validate(payload).model_dump(mode="json")
