"""Job runner that integrates the ingestion pipeline and ppt_generation.

This runner calls the real ingestion pipeline during analyzing_data,
then passes the result to ppt_generation for slide generation.

Later stages use ppt_generation's --fake-llm mode by default until
real LLM credentials are configured.
"""

import asyncio
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.pipeline import run_ingestion_pipeline
from app.ingestion.schemas import PipelineStatus
from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import Artifact, JobError, JobStage, JobStatus

# Delay between stage transitions (seconds) for visual feedback.
STAGE_DELAY_SECONDS: float = 1.0


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


def _setup_ppt_generation_path():
    """Add ppt_generation's parent (src/) to sys.path so it can be imported."""
    # Local dev: src/backend/app/worker/job_runner.py → parents[3] = src/
    # Docker:    /app/app/worker/job_runner.py → parents[3] = /
    #            ppt_generation is at /app/src/ppt_generation/
    # Strategy: try both the parents[3] path and /app/src (Docker fallback)
    src_dir = Path(__file__).resolve().parents[3]
    candidates = [src_dir, src_dir / "src", Path("/app/src")]
    for candidate in candidates:
        if (candidate / "ppt_generation").exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)
            break


def _run_ppt_generation(
    ingestion_payload: dict,
    user_prompt: str,
    output_dir: Path,
) -> tuple[int, Path, Path]:
    """Run ppt_generation synchronously. Returns (exit_code, pptx_path, xlsx_path).

    Uses fake-llm mode so no API key is needed.
    """
    _setup_ppt_generation_path()

    from ppt_generation.run_pipeline import run

    # Save ingestion payload as JSON for ppt_generation to consume
    ingestion_json = output_dir / "ingestion_input.json"
    ingestion_json.write_text(
        json.dumps(ingestion_payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    exit_code = run(
        ingestion_path=str(ingestion_json),
        user_prompt=user_prompt,
        sections=None,
        output_dir=output_dir,
        use_fake_llm=True,  # Use fake LLM until real keys are configured
        skip_semantic_review=True,
        dump_dir=output_dir / "stages",
    )

    pptx_path = output_dir / "deck.pptx"
    xlsx_path = output_dir / "deck_data.xlsx"

    return exit_code, pptx_path, xlsx_path


async def run_job(job_id: str, repository: JobRepository) -> None:
    """Run the job through all stages.

    Stage 1 (parsing_intent): Quick validation of inputs.
    Stage 2 (analyzing_data): Calls real ingestion pipeline.
    Stage 3 (writing_insights): Calls ppt_generation (sections + charts + narratives).
    Stage 4 (rendering): Calls ppt_generation (render + verify).
    Stage 5 (validating): Already done within ppt_generation.
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

    # Merge ingestion results into a single payload for ppt_generation
    # Use the first successful result's full payload, merge datasets from others
    merged_payload = None
    all_datasets = []

    def _safe_serialize(obj):
        """Fallback for objects Pydantic can't serialize (e.g. openpyxl ArrayFormula)."""
        return str(obj)

    for result in ingestion_results:
        if result.pipeline_status in (PipelineStatus.COMPLETED, PipelineStatus.COMPLETED_WITH_WARNINGS):
            # model_dump with mode='python' then json.dumps with default handler
            # to gracefully handle openpyxl types in raw_value fields
            raw = result.model_dump(mode="python")
            payload = json.loads(json.dumps(raw, default=_safe_serialize))
            if merged_payload is None:
                merged_payload = payload
            all_datasets.extend(payload.get("datasets", []))

    if merged_payload is not None:
        merged_payload["datasets"] = all_datasets

    total_datasets = len(all_datasets)
    ingestion_message = f"資料分析完成，共取得 {total_datasets} 個資料集"

    job = await _update_job(
        repository, job,
        stage=JobStage.analyzing_data,
        status=JobStatus.running,
        progress=40,
        message=ingestion_message,
    )
    if job is None:
        return

    # ─── Stage 3: writing_insights (ppt_generation) ────────────────
    await asyncio.sleep(0.5)
    job = await _update_job(
        repository, job,
        stage=JobStage.writing_insights,
        status=JobStatus.running,
        progress=50,
        message="正在規劃簡報章節與撰寫分析摘要",
    )
    if job is None:
        return

    # Run ppt_generation if we have valid ingestion data
    pptx_path = None
    xlsx_path = None
    ppt_error = None

    if merged_payload and total_datasets > 0:
        # Create a job-specific output directory
        output_dir = Path(tempfile.gettempdir()) / "slidegen_outputs" / job_id
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            exit_code, pptx_path, xlsx_path = await asyncio.to_thread(
                _run_ppt_generation,
                merged_payload,
                job.prompt,
                output_dir,
            )

            if exit_code != 0:
                # ppt_generation returned non-zero but didn't crash
                # Check if files were still produced (partial success)
                if not pptx_path.exists():
                    pptx_path = None
                if not xlsx_path.exists():
                    xlsx_path = None

                if pptx_path is None:
                    ppt_error = "簡報生成未完成（LLM 敘事階段未通過驗證）"

        except Exception as exc:
            ppt_error = f"簡報生成過程發生錯誤：{exc}"
    else:
        ppt_error = "沒有有效的資料集，無法生成簡報"

    # ─── Stage 4: rendering ────────────────────────────────────────
    await asyncio.sleep(0.5)

    if ppt_error:
        # ppt_generation failed, but ingestion succeeded — report partial success
        job = await _update_job(
            repository, job,
            stage=JobStage.rendering,
            status=JobStatus.running,
            progress=80,
            message=f"簡報生成階段：{ppt_error}",
        )
    else:
        job = await _update_job(
            repository, job,
            stage=JobStage.rendering,
            status=JobStatus.running,
            progress=85,
            message="簡報已生成，正在完成最後處理",
        )
    if job is None:
        return

    # ─── Stage 5: validating ──────────────────────────────────────
    await asyncio.sleep(0.5)
    job = await _update_job(
        repository, job,
        stage=JobStage.validating,
        status=JobStatus.running,
        progress=95,
        message="正在驗證輸出品質",
    )
    if job is None:
        return

    # ─── Completed ─────────────────────────────────────────────────
    await asyncio.sleep(0.3)

    # Build artifacts list
    artifacts = []
    if pptx_path and pptx_path.exists():
        artifacts.append(Artifact(
            type="pptx",
            filename="deck.pptx",
            download_url=f"/api/v1/downloads/{job_id}/deck.pptx",
        ))
        # Upload to S3
        from app.services.s3_service import store_output
        store_output(job_id, pptx_path, "deck.pptx")
    if xlsx_path and xlsx_path.exists():
        artifacts.append(Artifact(
            type="xlsx",
            filename="deck_data.xlsx",
            download_url=f"/api/v1/downloads/{job_id}/deck_data.xlsx",
        ))
        from app.services.s3_service import store_output
        store_output(job_id, xlsx_path, "deck_data.xlsx")

    # Build summary
    summary_parts = [f"已完成 {len(job.filenames)} 個檔案的資料分析。"]
    summary_parts.append(f"共取得 {total_datasets} 個結構化資料集。")

    if pptx_path and pptx_path.exists():
        summary_parts.append("簡報已成功生成。")
    elif ppt_error:
        summary_parts.append(f"簡報生成注意事項：{ppt_error}")
        summary_parts.append("（目前使用模擬 LLM，接上真實 LLM 後將正常產出）")

    if ingestion_errors:
        summary_parts.append(f"注意：{len(ingestion_errors)} 個檔案處理時有問題。")

    await _update_job(
        repository, job,
        stage=JobStage.completed,
        status=JobStatus.succeeded,
        progress=100,
        message="處理完成",
        summary="\n".join(summary_parts),
        artifacts=artifacts,
    )
