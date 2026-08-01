from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ingestion.router import (
    router as ingestion_router,
)
from app.api.router import router as api_router
from app.auth import auth_router
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

# Ingestion router already owns the /ingestion prefix.
app.include_router(ingestion_router)

# Auth routes (/auth/login, /auth/register)
app.include_router(auth_router)

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
        "ingestion_schema": False,
        "ingestion_pipeline": False,
        "generation_pipeline": False,
        "s3_configuration": False,
    }

    try:
        from app.ingestion.schemas import UnifiedIngestionResult
        from app.ingestion.pipeline import run_ingestion_pipeline
        from core.generation_orchestrator import generate_deck

        checks["ingestion_schema"] = UnifiedIngestionResult is not None
        checks["ingestion_pipeline"] = run_ingestion_pipeline is not None
        checks["generation_pipeline"] = generate_deck is not None
        checks["s3_configuration"] = bool(
            settings.s3_bucket.strip() and settings.aws_region.strip()
        )
    except Exception as error:
        return {
            "status": "not_ready",
            "checks": checks,
            "error": str(error),
        }

    ready = all(checks.values())
    payload = {
        "status": "ready" if ready else "not_ready",
        "checks": checks,
    }

    if not checks["s3_configuration"]:
        payload["error"] = "S3_BUCKET 與 AWS_REGION 必須設定後才能接受生成工作"

    return payload