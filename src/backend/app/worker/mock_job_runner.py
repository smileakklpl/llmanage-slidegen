"""Mock job runner that simulates job progression through stages.

This module is used during development to verify the polling and
progress-tracking flow without real AI/data pipelines. It will be
replaced by a proper worker queue (SQS + Lambda/ECS) in production.
"""

import asyncio
from datetime import datetime, timezone

from app.repositories.job_repository import JobModel, JobRepository
from app.schemas.jobs import Artifact, JobStage, JobStatus

# Each tuple: (stage, status, progress, message)
_STAGES: list[tuple[JobStage, JobStatus, int, str]] = [
    (JobStage.queued, JobStatus.queued, 0, "工作已排入佇列"),
    (JobStage.parsing_intent, JobStatus.running, 15, "正在解析使用者意圖"),
    (JobStage.analyzing_data, JobStatus.running, 35, "正在分析資料"),
    (JobStage.writing_insights, JobStatus.running, 60, "正在撰寫分析摘要"),
    (JobStage.rendering, JobStatus.running, 80, "正在生成簡報"),
    (JobStage.validating, JobStatus.running, 92, "正在驗證輸出品質"),
    (JobStage.completed, JobStatus.succeeded, 100, "簡報生成完成"),
]

# Delay between stage transitions in seconds.
STAGE_DELAY_SECONDS: float = 1.5


async def run_mock_job(job_id: str, repository: JobRepository) -> None:
    """Simulate job progress through stages.

    This coroutine updates the job in the repository at each stage,
    sleeping between transitions so that polling clients can observe
    intermediate states.

    Args:
        job_id: The unique identifier of the job to progress.
        repository: The repository instance used to persist updates.
    """
    # Skip the first stage (queued) since the job is already created in that state.
    for stage, status, progress, message in _STAGES[1:]:
        await asyncio.sleep(STAGE_DELAY_SECONDS)

        job = await repository.get(job_id)
        if job is None:
            # Job was deleted or doesn't exist; abort silently.
            return

        # When the job reaches the final completed stage, attach fake artifacts.
        if status == JobStatus.succeeded and progress == 100:
            artifacts = [
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
            ]
            summary = (
                "已根據您提供的資料完成簡報生成。本次簡報包含以下重點：\n\n"
                "1. 依據上傳的 Excel 資料，提取了關鍵數據趨勢與指標\n"
                "2. 產出 12 頁簡報，涵蓋摘要、趨勢圖表、詳細數據分析\n"
                "3. 附帶完整的圖表資料 Excel 檔供進一步使用\n\n"
                "如需調整內容或格式，請修改提示詞後重新生成。"
            )
        else:
            artifacts = job.artifacts
            summary = job.summary

        updated_job = JobModel(
            job_id=job.job_id,
            status=status,
            stage=stage,
            progress=progress,
            message=message,
            created_at=job.created_at,
            updated_at=datetime.now(timezone.utc),
            artifacts=artifacts,
            error=job.error,
            summary=summary,
            prompt=job.prompt,
            filenames=job.filenames,
        )
        await repository.update(updated_job)
