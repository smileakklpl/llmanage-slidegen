from datetime import datetime, timezone

from fastapi import FastAPI

from app.ingestion.router import (
    router as ingestion_router,
)


app = FastAPI(
    title="智匯數據簡報神器",
)


app.include_router(
    ingestion_router,
    prefix="/ingestion",
    tags=["ingestion"],
)


@app.get("/health")
async def health_check() -> dict:
    return {
        "status": "ok",
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
    }


@app.get("/ready")
async def readiness_check() -> dict:
    checks = {
        "schemas": False,
        "pipeline": False,
    }

    try:
        from app.ingestion.schemas import (
            UnifiedIngestionResult,
        )

        checks["schemas"] = (
            UnifiedIngestionResult
            is not None
        )

        from app.ingestion.pipeline import (
            run_ingestion_pipeline,
        )

        checks["pipeline"] = (
            run_ingestion_pipeline
            is not None
        )

    except Exception as error:
        return {
            "status": "not_ready",
            "checks": checks,
            "error": str(error),
        }

    return {
        "status": (
            "ready"
            if all(checks.values())
            else "not_ready"
        ),
        "checks": checks,
    }