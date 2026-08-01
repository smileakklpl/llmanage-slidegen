"""Callable orchestration boundary around the existing PPT generation pipeline."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts.generation import (
    GeneratedArtifact,
    GenerationRequest,
    GenerationResult,
    NormalizedIngestionContract,
)
from ppt_generation import run_pipeline
from ppt_generation.contracts import DeckSpecContract
from ppt_generation.contracts.stages import PipelineRequestContract


class GenerationFailedError(RuntimeError):
    """Raised when generation did not produce a fully verified artifact set."""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise GenerationFailedError(f"缺少必要階段輸出：{path.name}")

    payload = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(payload, dict):
        raise GenerationFailedError(f"階段輸出格式錯誤：{path.name}")

    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def _artifact(path: Path, artifact_type: str, media_type: str) -> GeneratedArtifact:
    if not path.is_file():
        raise GenerationFailedError(f"生成成功但找不到輸出檔：{path.name}")

    return GeneratedArtifact(
        artifact_type=artifact_type,  # type: ignore[arg-type]
        filename=path.name,
        path=str(path),
        media_type=media_type,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )


def _is_review_deliverable(
    item: dict[str, Any], generation_policy: str
) -> bool:
    """Accept warning pages only with explicit selected-candidate hard evidence."""
    delivery_status = item.get("delivery_status")

    if delivery_status == "APPROVED_WITH_WARNING":
        return bool(
            generation_policy == "required"
            and item.get("hard_rules_passed") is True
            and item.get("selected_candidate_hard_issues") == []
        )

    return bool(
        item.get("status") == "APPROVED"
        and delivery_status in {None, "APPROVED"}
    )


def generate_deck(request: dict[str, Any]) -> GenerationResult:
    """Run the production pipeline from a schema-validated JSON request.

    Backend owns raw-file ingestion. This boundary accepts only normalized
    ingestion JSON and fails closed on review, placeholders, and T1 coverage.
    """

    validated = GenerationRequest.model_validate(request)
    ingestion_path = Path(validated.ingestion_path).resolve()
    output_dir = Path(validated.output_dir).resolve()

    if not ingestion_path.is_file():
        raise GenerationFailedError(f"ingestion JSON 不存在：{ingestion_path}")

    ingestion = NormalizedIngestionContract.model_validate(
        _read_json(ingestion_path)
    )
    blocked_datasets = [
        dataset.dataset_id
        for dataset in ingestion.datasets
        if dataset.requires_human_review
        or dataset.review_status in {"pending", "rejected"}
    ]
    if blocked_datasets:
        raise GenerationFailedError(
            "資料集尚未通過人工確認，禁止進入生成流程："
            + "、".join(blocked_datasets)
        )
    ingestion_payload = ingestion.model_dump(mode="json")

    options = validated.options
    remaining_deadline = options.deadline_seconds
    llm_budget_exhausted = False
    if validated.deadline_at_utc is not None:
        deadline_at = validated.deadline_at_utc
        if deadline_at.tzinfo is None:
            deadline_at = deadline_at.replace(tzinfo=timezone.utc)
        raw_remaining = (
            deadline_at - datetime.now(timezone.utc)
        ).total_seconds()
        llm_budget_exhausted = raw_remaining <= 0
        remaining_deadline = max(0.1, raw_remaining)
    render_reserve = min(
        options.render_reserve_seconds,
        max(0.0, remaining_deadline - 0.05),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = output_dir / "stages"

    pipeline_request = PipelineRequestContract(
        ingestion_payload=ingestion_payload,
        ingestion_source=str(ingestion_path),
        user_prompt=validated.prompt,
        sections=validated.sections,
        output_dir=str(output_dir),
        use_fake_llm=options.use_fake_llm,
        skip_semantic_review=options.skip_semantic_review,
        stop_after="verify",
        dump_dir=str(stage_dir),
        deck_title=validated.deck_title,
        generation_policy=options.policy,
        generation_deadline_seconds=remaining_deadline,
        generation_render_reserve_seconds=render_reserve,
        generation_llm_budget_exhausted=llm_budget_exhausted,
        source_objects=[
            item.model_dump(mode="json") for item in validated.source_objects
        ],
    )
    exit_code = run_pipeline.run_from_contract(
        pipeline_request.model_dump(mode="json")
    )

    if exit_code != 0:
        raise GenerationFailedError(f"PPT pipeline 失敗，exit_code={exit_code}")

    review = _read_json(stage_dir / "05_review.json")
    manifest = _read_json(output_dir / "generation_manifest.json")
    DeckSpecContract.model_validate(_read_json(output_dir / "deckspec.json"))
    generation_policy = (
        manifest.get("request", {}).get("generation_policy") or "strict"
    )

    rejected = [
        item
        for item in review.get("reviews", [])
        if not _is_review_deliverable(item, generation_policy)
    ]

    if rejected:
        pages = [item.get("page_number") for item in rejected]
        raise GenerationFailedError(f"審查退件頁面不得輸出：{pages}")

    render = _read_json(stage_dir / "06_render.json")
    placeholder_errors = render.get("placeholder_errors") or {}

    if placeholder_errors:
        raise GenerationFailedError(
            f"仍有未代入的敘事 placeholder：{placeholder_errors}"
        )

    verification = _read_json(stage_dir / "07_verify.json")

    if verification.get("passed") is not True:
        raise GenerationFailedError("T1 數值一致性驗證未通過")

    warnings = verification.get("warnings") or []

    if warnings:
        raise GenerationFailedError(f"T1 驗證含未處理警告：{warnings}")

    series_checked = int(verification.get("series_checked") or 0)
    external_checked = int(verification.get("external_checked") or 0)

    if series_checked <= 0 or external_checked != series_checked:
        raise GenerationFailedError(
            "T1 未完整比對外部稽核 Excel："
            f"series={series_checked}, external={external_checked}"
        )

    verification_path = output_dir / "verification.json"
    shutil.copyfile(stage_dir / "07_verify.json", verification_path)

    artifacts = [
        _artifact(
            output_dir / "deck.pptx",
            "pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        _artifact(
            output_dir / "deck_data.xlsx",
            "xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        _artifact(verification_path, "json", "application/json"),
        _artifact(
            output_dir / "generation_manifest.json",
            "json",
            "application/json",
        ),
    ]

    return GenerationResult(
        job_id=validated.job_id,
        artifacts=artifacts,
        verification_passed=True,
        series_checked=series_checked,
        external_checked=external_checked,
        page_count=int(render.get("page_count") or 0),
        slide_count=int(render.get("slide_count") or 0),
        chart_count=int(render.get("chart_count") or 0),
    )
