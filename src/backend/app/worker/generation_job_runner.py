"""Asynchronous job runner connecting S3 inputs to ingestion and PPT generation."""

from __future__ import annotations

import asyncio
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.ingestion import generation_bridge
from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import Artifact, JobError, JobStage, JobStatus
from app.storage.s3_storage import S3ObjectStorage
from core.contracts.generation import GenerationRequest, NormalizedIngestionContract, StoredObjectRef
from core.generation_orchestrator import generate_deck


def _prepare_ingestion_for_review(payload: dict) -> dict:
    """Require a user confirmation pass for every extracted dataset.

    ``requires_human_review`` keeps its original meaning: it is a risk signal
    (for example, confidence below the auto-accept threshold) used by the UI to
    highlight data that deserves extra attention. ``review_status`` is the
    workflow gate. Every freshly ingested dataset becomes ``pending`` so even
    high-confidence data can be inspected and corrected before generation.
    """
    ingestion = NormalizedIngestionContract.model_validate(payload)
    datasets = [
        dataset.model_copy(update={"review_status": "pending"})
        for dataset in ingestion.datasets
    ]
    return ingestion.model_copy(update={"datasets": datasets}).model_dump(
        mode="json"
    )


def _blocked_dataset_ids(payload: dict) -> list[str]:
    ingestion = NormalizedIngestionContract.model_validate(payload)
    return [
        dataset.dataset_id
        for dataset in ingestion.datasets
        if dataset.requires_human_review
        or dataset.review_status in {"pending", "rejected"}
    ]


def _total_deadline_at(job: JobModel) -> datetime:
    created_at = job.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return created_at + timedelta(seconds=job.generation_options.deadline_seconds)


def _pipeline_deadline_at(job: JobModel) -> datetime:
    return _total_deadline_at(job) - timedelta(
        seconds=job.generation_options.output_reserve_seconds
    )


def _ensure_before_deadline(
    job: JobModel,
    *,
    phase: str,
    pipeline: bool = False,
) -> None:
    deadline = _pipeline_deadline_at(job) if pipeline else _total_deadline_at(job)
    if datetime.now(timezone.utc) >= deadline:
        scope = "pipeline" if pipeline else "job"
        raise TimeoutError(f"{scope} deadline 已到，停止進入 {phase}")


async def _transition(
    job_id: str,
    repository: JobRepository,
    *,
    status: JobStatus,
    stage: JobStage,
    progress: int,
    message: str,
    artifacts: list[Artifact] | None = None,
    error: JobError | None = None,
    summary: str | None = None,
    ingestion_object: StoredObjectRef | None = None,
    review_required_count: int | None = None,
) -> None:
    job = await repository.get(job_id)
    if job is None:
        return

    changes = {
        "status": status,
        "stage": stage,
        "progress": progress,
        "message": message,
        "updated_at": datetime.now(timezone.utc),
        "artifacts": artifacts if artifacts is not None else job.artifacts,
        "error": error,
        "summary": summary if summary is not None else job.summary,
        "review_required_count": (
            review_required_count
            if review_required_count is not None
            else job.review_required_count
        ),
    }
    if ingestion_object is not None:
        changes["ingestion_object"] = ingestion_object

    await repository.update(job.model_copy(update=changes))


async def _materialize_template(
    job: JobModel,
    workspace: Path,
    storage: S3ObjectStorage,
) -> Path | None:
    """Download the optional template separately from ingestion inputs."""
    if job.template_object is None:
        return None

    destination = workspace / "template.pptx"
    await asyncio.to_thread(
        storage.download_path,
        job.template_object.key,
        destination,
    )
    return destination


async def _generate_from_ingestion_path(
    *,
    job: JobModel,
    ingestion_path: Path,
    template_path: Path | None,
    output_dir: Path,
    repository: JobRepository,
    storage: S3ObjectStorage,
) -> None:
    """Run deterministic/LLM generation after ingestion is approved."""
    _ensure_before_deadline(job, phase="generation", pipeline=True)
    await _transition(
        job.job_id,
        repository,
        status=JobStatus.running,
        stage=JobStage.rendering,
        progress=55,
        message="資料已確認，正在計算指標並生成原生 PowerPoint 圖表",
        review_required_count=0,
    )

    # Keep S3 upload and the final safety buffer outside the callable pipeline.
    # The SLA origin is the persisted Job timestamp returned with 202.
    deadline_at = _pipeline_deadline_at(job)
    request = GenerationRequest(
        job_id=job.job_id,
        prompt=job.prompt,
        ingestion_path=str(ingestion_path),
        template_path=str(template_path) if template_path is not None else None,
        output_dir=str(output_dir),
        source_objects=job.input_objects,
        options=job.generation_options,
        deadline_at_utc=deadline_at,
    )
    result = await asyncio.to_thread(
        generate_deck,
        request.model_dump(mode="json"),
    )
    _ensure_before_deadline(job, phase="artifact upload")

    await _transition(
        job.job_id,
        repository,
        status=JobStatus.running,
        stage=JobStage.validating,
        progress=90,
        message="驗證已通過，正在保存產出",
    )

    uploaded_by_path: dict[Path, str] = {}
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        _ensure_before_deadline(job, phase=f"upload {path.name}")
        relative = path.relative_to(output_dir).as_posix()
        stored = await asyncio.to_thread(
            storage.upload_path,
            path,
            key=f"outputs/{job.job_id}/{relative}",
        )
        _ensure_before_deadline(job, phase=f"upload {path.name} completion")
        uploaded_by_path[path.resolve()] = stored.key

    deck_spec_path = output_dir / "deckspec.json"
    if deck_spec_path.is_file():
        _ensure_before_deadline(job, phase="deckspec upload")
        await asyncio.to_thread(
            storage.upload_path,
            deck_spec_path,
            key=f"deckspecs/{job.job_id}/deckspec.json",
            content_type="application/json",
        )
        _ensure_before_deadline(job, phase="deckspec upload completion")

    artifacts: list[Artifact] = []
    for generated in result.artifacts:
        generated_path = Path(generated.path).resolve()
        artifacts.append(
            Artifact(
                type=generated.artifact_type,
                filename=generated.filename,
                download_url="",
                object_key=uploaded_by_path[generated_path],
                sha256=generated.sha256,
                size_bytes=generated.size_bytes,
            )
        )

    summary = (
        f"已完成 {result.page_count} 個內容頁、{result.chart_count} 張圖表，"
        f"共 {result.slide_count} 張投影片；"
        f"T1 已核對 {result.series_checked} 個數值系列。"
    )
    _ensure_before_deadline(job, phase="success transition")
    await _transition(
        job.job_id,
        repository,
        status=JobStatus.succeeded,
        stage=JobStage.completed,
        progress=100,
        message="簡報生成與數值驗證完成",
        artifacts=artifacts,
        summary=summary,
        review_required_count=0,
    )


async def run_generation_job(
    job_id: str,
    repository: JobRepository,
    storage: S3ObjectStorage,
) -> None:
    """Ingest raw S3 uploads and always pause for pre-generation review."""
    try:
        job = await repository.get(job_id)
        if job is None:
            return
        _ensure_before_deadline(job, phase="ingestion")

        await _transition(
            job_id,
            repository,
            status=JobStatus.running,
            stage=JobStage.analyzing_data,
            progress=20,
            message="正在下載並解析上傳資料",
        )

        with tempfile.TemporaryDirectory(prefix=f"slidegen-{job_id}-") as temp_name:
            workspace = Path(temp_name)
            upload_dir = workspace / "uploads"
            output_dir = workspace / "outputs"
            template_path = await _materialize_template(job, workspace, storage)
            materialized: list[tuple[Path, str]] = []

            for index, object_ref in enumerate(job.input_objects, start=1):
                _ensure_before_deadline(
                    job,
                    phase=f"download {object_ref.filename}",
                    pipeline=True,
                )
                destination = upload_dir / f"{index:02d}_{object_ref.filename}"
                await asyncio.to_thread(
                    storage.download_path,
                    object_ref.key,
                    destination,
                )
                materialized.append((destination, object_ref.filename))

            if not materialized:
                raise RuntimeError("job 沒有任何可處理的 S3 輸入")

            pipeline_deadline_at = _pipeline_deadline_at(job)
            remaining_pipeline_seconds = max(
                0.0,
                (pipeline_deadline_at - datetime.now(timezone.utc)).total_seconds(),
            )
            ocr_budget_seconds = min(
                job.generation_options.ocr_max_seconds,
                remaining_pipeline_seconds,
            )
            ocr_deadline_monotonic = time.monotonic() + ocr_budget_seconds
            ingestion_payload = await asyncio.to_thread(
                generation_bridge.ingest_inputs,
                materialized,
                ocr_deadline_monotonic=ocr_deadline_monotonic,
                ocr_max_pages=job.generation_options.ocr_max_pages,
            )
            _ensure_before_deadline(job, phase="ingestion persistence", pipeline=True)
            ingestion_path = generation_bridge.save_payload(
                ingestion_payload,
                workspace / "ingestion.json",
            )
            ingestion_object = await asyncio.to_thread(
                storage.upload_path,
                ingestion_path,
                key=f"uploads/{job_id}/ingestion.json",
                content_type="application/json",
            )

            ingestion = NormalizedIngestionContract.model_validate(
                ingestion_payload
            )
            pending_count = len(ingestion.datasets)
            low_confidence_count = sum(
                1
                for dataset in ingestion.datasets
                if dataset.requires_human_review
            )
            if low_confidence_count:
                message = (
                    f"資料抽取完成；請確認 {pending_count} 個資料集後再生成，"
                    f"其中 {low_confidence_count} 個資料集需要特別留意"
                )
            else:
                message = (
                    f"資料抽取完成；請確認 {pending_count} 個資料集後再生成"
                )

            await _transition(
                job_id,
                repository,
                status=JobStatus.waiting_review,
                stage=JobStage.reviewing_data,
                progress=45,
                message=message,
                ingestion_object=ingestion_object,
                review_required_count=0,
            )

            refreshed = await repository.get(job_id)
            if refreshed is None:
                return
            await _generate_from_ingestion_path(
                job=refreshed,
                ingestion_path=ingestion_path,
                template_path=template_path,
                output_dir=output_dir,
                repository=repository,
                storage=storage,
            )
            return

    except Exception as error:  # noqa: BLE001 - worker must persist all failures
        await _transition(
            job_id,
            repository,
            status=JobStatus.failed,
            stage=JobStage.failed,
            progress=100,
            message="簡報生成失敗",
            error=JobError(
                code="GENERATION_FAILED",
                message=f"{type(error).__name__}: {error}",
            ),
        )


async def resume_generation_job(
    job_id: str,
    repository: JobRepository,
    storage: S3ObjectStorage,
) -> None:
    """Resume a job from its persisted, human-reviewed ingestion JSON."""
    try:
        job = await repository.get(job_id)
        if job is None:
            return
        _ensure_before_deadline(job, phase="review resume", pipeline=True)
        if job.ingestion_object is None:
            raise RuntimeError("job 尚未保存 ingestion.json")

        with tempfile.TemporaryDirectory(prefix=f"slidegen-resume-{job_id}-") as temp_name:
            workspace = Path(temp_name)
            ingestion_path = workspace / "ingestion.json"
            output_dir = workspace / "outputs"
            template_path = await _materialize_template(job, workspace, storage)
            await asyncio.to_thread(
                storage.download_path,
                job.ingestion_object.key,
                ingestion_path,
            )

            payload = generation_bridge.load_payload(ingestion_path)
            blocked = _blocked_dataset_ids(payload)
            if blocked:
                raise RuntimeError(
                    "仍有未通過人工確認的資料集：" + "、".join(blocked)
                )

            await _generate_from_ingestion_path(
                job=job,
                ingestion_path=ingestion_path,
                template_path=template_path,
                output_dir=output_dir,
                repository=repository,
                storage=storage,
            )
    except Exception as error:  # noqa: BLE001
        await _transition(
            job_id,
            repository,
            status=JobStatus.failed,
            stage=JobStage.failed,
            progress=100,
            message="續跑簡報生成失敗",
            error=JobError(
                code="GENERATION_RESUME_FAILED",
                message=f"{type(error).__name__}: {error}",
            ),
        )
