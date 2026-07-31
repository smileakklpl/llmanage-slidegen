"""Real asynchronous job runner connecting S3 inputs to the PPT pipeline."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.repositories.job_repository import JobRepository
from app.schemas.jobs import Artifact, JobError, JobStage, JobStatus
from app.storage.s3_storage import S3ObjectStorage
from core.contracts.generation import GenerationRequest
from core.generation_orchestrator import generate_deck


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
) -> None:
    job = await repository.get(job_id)

    if job is None:
        return

    updated = job.model_copy(
        update={
            "status": status,
            "stage": stage,
            "progress": progress,
            "message": message,
            "updated_at": datetime.now(timezone.utc),
            "artifacts": artifacts if artifacts is not None else job.artifacts,
            "error": error,
            "summary": summary if summary is not None else job.summary,
        }
    )
    await repository.update(updated)


async def run_generation_job(
    job_id: str,
    repository: JobRepository,
    storage: S3ObjectStorage,
    *,
    use_fake_llm: bool,
    skip_semantic_review: bool,
) -> None:
    """Materialize S3 uploads, generate the deck, and persist all outputs to S3."""

    try:
        job = await repository.get(job_id)

        if job is None:
            return

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
            local_inputs: list[Path] = []

            for index, object_ref in enumerate(job.input_objects, start=1):
                destination = upload_dir / f"{index:02d}_{object_ref.filename}"
                await asyncio.to_thread(
                    storage.download_path,
                    object_ref.key,
                    destination,
                )
                local_inputs.append(destination)

            if not local_inputs:
                raise RuntimeError("job 沒有任何可處理的 S3 輸入")

            input_path = local_inputs[0] if len(local_inputs) == 1 else upload_dir

            await _transition(
                job_id,
                repository,
                status=JobStatus.running,
                stage=JobStage.rendering,
                progress=55,
                message="正在計算指標並生成原生 PowerPoint 圖表",
            )

            request = GenerationRequest(
                job_id=job_id,
                prompt=job.prompt,
                input_path=str(input_path),
                output_dir=str(output_dir),
                use_fake_llm=use_fake_llm,
                skip_semantic_review=skip_semantic_review,
            )
            result = await asyncio.to_thread(generate_deck, request)

            await _transition(
                job_id,
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

                relative = path.relative_to(output_dir).as_posix()
                key = f"outputs/{job_id}/{relative}"
                stored = await asyncio.to_thread(
                    storage.upload_path,
                    path,
                    key=key,
                )
                uploaded_by_path[path.resolve()] = stored.key

            artifacts: list[Artifact] = []

            for generated in result.artifacts:
                generated_path = Path(generated.path).resolve()
                stored_key = uploaded_by_path[generated_path]
                artifacts.append(
                    Artifact(
                        type=generated.artifact_type,
                        filename=generated.filename,
                        # Durable state stores no bearer credential. API reads
                        # create a fresh presigned URL from object_key.
                        download_url="",
                        object_key=stored_key,
                        sha256=generated.sha256,
                        size_bytes=generated.size_bytes,
                    )
                )

            summary = (
                f"已完成 {result.page_count} 個內容頁、{result.chart_count} 張圖表，"
                f"共 {result.slide_count} 張投影片；"
                f"T1 已核對 {result.series_checked} 個數值系列。"
            )

            await _transition(
                job_id,
                repository,
                status=JobStatus.succeeded,
                stage=JobStage.completed,
                progress=100,
                message="簡報生成與數值驗證完成",
                artifacts=artifacts,
                summary=summary,
            )

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
