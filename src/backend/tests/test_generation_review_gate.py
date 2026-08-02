"""Pre-generation review gate for durable jobs."""

from app.worker.generation_job_runner import (
    _blocked_dataset_ids,
    _prepare_ingestion_for_review,
)


def _payload(*, confidence: float, requires_human_review: bool) -> dict:
    return {
        "contract_version": "1.0",
        "filename": "input.xlsx",
        "pipeline_status": "completed",
        "source_files": ["input.xlsx"],
        "datasets": [
            {
                "dataset_id": "Sheet1",
                "name": "Sheet1",
                "filename": "input.xlsx",
                "source_kind": "excel",
                "table_kind": "tabular",
                "metadata": {},
                "columns": [
                    {
                        "key": "revenue",
                        "label": "Revenue",
                        "index": 0,
                        "data_type": "number",
                    }
                ],
                "records": [],
                "confidence": confidence,
                "requires_human_review": requires_human_review,
                "review_status": "not_required",
                "warnings": [],
            }
        ],
        "warnings": [],
        "errors": [],
    }


def test_high_confidence_dataset_still_enters_confirmation_gate() -> None:
    prepared = _prepare_ingestion_for_review(
        _payload(confidence=0.98, requires_human_review=False)
    )

    dataset = prepared["datasets"][0]
    assert dataset["review_status"] == "pending"
    assert dataset["requires_human_review"] is False
    assert _blocked_dataset_ids(prepared) == ["Sheet1"]


def test_low_confidence_signal_is_preserved_while_review_becomes_pending() -> None:
    prepared = _prepare_ingestion_for_review(
        _payload(confidence=0.72, requires_human_review=True)
    )

    dataset = prepared["datasets"][0]
    assert dataset["review_status"] == "pending"
    assert dataset["requires_human_review"] is True
    assert _blocked_dataset_ids(prepared) == ["Sheet1"]
