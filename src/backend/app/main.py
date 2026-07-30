from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ingestion.router import (
    router as ingestion_router,
)
from app.api.router import router as api_router
from app.core.config import settings
from app.core.errors import register_error_handlers


app = FastAPI(
    title="智匯數據簡報神器",
)

# CORS — 前端開發需要
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全域錯誤處理
register_error_handlers(app)

# 原有的 ingestion 路由
app.include_router(
    ingestion_router,
    prefix="/ingestion",
    tags=["ingestion"],
)

# Web UI 的 job 路由 (/api/v1/jobs/...)
app.include_router(api_router)


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