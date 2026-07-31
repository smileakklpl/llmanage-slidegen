"""Schema-validated JSON contracts for ppt_generation module boundaries."""

from .stages import (
    ChartStageContract,
    DeckSpecContract,
    MetricStoreContract,
    PageNarrativeContract,
    ReviewContract,
    SectionStageContract,
    chart_stage_payload,
    deck_spec_payload,
    metric_store_payload,
    narrative_payload,
    review_payload,
    section_stage_payload,
)

__all__ = [
    "ChartStageContract",
    "DeckSpecContract",
    "MetricStoreContract",
    "PageNarrativeContract",
    "ReviewContract",
    "SectionStageContract",
    "chart_stage_payload",
    "deck_spec_payload",
    "metric_store_payload",
    "narrative_payload",
    "review_payload",
    "section_stage_payload",
]
