"""Job runner that integrates the ingestion pipeline.

This runner replaces the mock runner for the analyzing_data stage,
calling the real ingestion pipeline to process uploaded files.
Later stages (writing_insights, rendering, validating) remain mocked
until the core pipeline and ppt_generation modules are ready to integrate.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.pipeline import run_ingestion_pipeline
from app.ingestion.schemas import PipelineStatus
from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import Artifact, JobError, JobStage, JobStatus

# Delay between stage transitions (seconds) for stages that are still mocked.
STAGE_DELAY_SECONDS: float = 1.5


async def _update_job(
    repository: JobRepository,
    job: JobModel,
    *,
    stage: JobStage,
    status: JobStatus,
    progress: int,
    message: str,
    **kwargs,
) -> JobModel | None:
    """Helper to update job state."""
    updated = JobModel(
        job_id=job.job_id,
        status=status,
        stage=stage,
        progress=progress,
        message=message,
        created_at=job.created_at,
        updated_at=datetime.now(timezone.utc),
        artifacts=kwargs.get("artifacts", job.artifacts),
        error=kwargs.get("error", job.error),
        summary=kwargs.get("summary", job.summary),
        prompt=job.prompt,
        filenames=job.filenames,
        file_paths=job.file_paths,
    )
    return await repository.update(updated)


async def run_job(job_id: str, repository: JobRepository) -> None:
    """Run the job through all stages.

    Stage 1 (parsing_intent): Quick validation of inputs.
    Stage 2 (analyzing_data): Calls real ingestion pipeline.
    Stage 3-5 (writing_insights, rendering, validating): Still mocked.
    """
    job = await repository.get(job_id)
    if job is None:
        return

    # ─── Stage 1: parsing_intent ───────────────────────────────────
    await asyncio.sleep(0.5)
    job = await _update_job(
        repository, job,
        stage=JobStage.parsing_intent,
        status=JobStatus.running,
        progress=10,
        message="正在解析使用者意圖",
    )
    if job is None:
        return

    # ─── Stage 2: analyzing_data (REAL ingestion) ──────────────────
    await asyncio.sleep(0.3)
    job = await _update_job(
        repository, job,
        stage=JobStage.analyzing_data,
        status=JobStatus.running,
        progress=20,
        message="正在分析上傳的資料檔案",
    )
    if job is None:
        return

    # Run ingestion on each uploaded file
    ingestion_results = []
    ingestion_errors = []

    for file_path in job.file_paths:
        path = Path(file_path)
        if not path.exists():
            ingestion_errors.append(f"檔案不存在：{path.name}")
            continue

        try:
            # run_ingestion_pipeline is synchronous, run in thread
            result = await asyncio.to_thread(
                run_ingestion_pipeline,
                file_path=path,
                original_filename=path.name,
            )
            ingestion_results.append(result)

            if result.pipeline_status == PipelineStatus.FAILED:
                ingestion_errors.append(
                    f"{path.name}：處理失敗 — {'; '.join(result.errors)}"
                )
            elif result.pipeline_status == PipelineStatus.REJECTED:
                ingestion_errors.append(
                    f"{path.name}：檔案被拒絕 — {'; '.join(result.errors)}"
                )

        except Exception as exc:
            ingestion_errors.append(f"{path.name}：{exc}")

    # If all files failed, mark job as failed
    if ingestion_errors and not any(
        r.pipeline_status in (PipelineStatus.COMPLETED, PipelineStatus.COMPLETED_WITH_WARNINGS)
        for r in ingestion_results
    ):
        await _update_job(
            repository, job,
            stage=JobStage.failed,
            status=JobStatus.failed,
            progress=25,
            message="資料分析失敗",
            error=JobError(
                code="INGESTION_FAILED",
                message="；".join(ingestion_errors),
            ),
        )
        return

    # Update progress after successful ingestion
    total_datasets = sum(len(r.datasets) for r in ingestion_results)
    ingestion_message = f"資料分析完成，共取得 {total_datasets} 個資料集"
    if ingestion_errors:
        ingestion_message += f"（{len(ingestion_errors)} 個檔案有問題）"

    job = await _update_job(
        repository, job,
        stage=JobStage.analyzing_data,
        status=JobStatus.running,
        progress=40,
        message=ingestion_message,
    )
    if job is None:
        return

    # ─── Stage 3: writing_insights (MOCKED) ───────────────────────
    await asyncio.sleep(STAGE_DELAY_SECONDS)
    job = await _update_job(
        repository, job,
        stage=JobStage.writing_insights,
        status=JobStatus.running,
        progress=60,
        message="正在撰寫分析摘要（待接 core pipeline）",
    )
    if job is None:
        return

    # ─── Stage 4: rendering (MOCKED) ──────────────────────────────
    await asyncio.sleep(STAGE_DELAY_SECONDS)
    job = await _update_job(
        repository, job,
        stage=JobStage.rendering,
        status=JobStatus.running,
        progress=80,
        message="正在生成簡報（待接 ppt_generation）",
    )
    if job is None:
        return

    # ─── Stage 5: validating (MOCKED) ─────────────────────────────
    await asyncio.sleep(STAGE_DELAY_SECONDS)
    job = await _update_job(
        repository, job,
        stage=JobStage.validating,
        status=JobStatus.running,
        progress=92,
        message="正在驗證輸出品質（待接 validator）",
    )
    if job is None:
        return

    # ─── Completed ─────────────────────────────────────────────────
    await asyncio.sleep(0.5)

    # Build summary from ingestion results
    summary_parts = [
        f"已完成 {len(job.filenames)} 個檔案的資料分析。",
        f"共取得 {total_datasets} 個結構化資料集。",
    ]
    if ingestion_errors:
        summary_parts.append(f"注意：{len(ingestion_errors)} 個檔案處理時有問題。")
    summary_parts.append("\n後續階段（摘要撰寫、簡報生成、品質驗證）目前為模擬模式，待接上對應模組。")

    await _update_job(
        repository, job,
        stage=JobStage.completed,
        status=JobStatus.succeeded,
        progress=100,
        message="處理完成",
        summary="\n".join(summary_parts),
        artifacts=[
            Artifact(
                type="pptx",
                filename="presentation.pptx",
                download_url="/api/v1/downloads/mock-pptx",
            ),
            Artifact(
                type="xlsx",
                filename="chart-data.xlsx",
                download_url="/api/v1/downloads/mock-xlsx",
            ),
        ],
    )
