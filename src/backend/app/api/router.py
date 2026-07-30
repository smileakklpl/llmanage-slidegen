"""Main API v1 router that aggregates all sub-routers."""

from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.jobs import router as jobs_router

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
router.include_router(jobs_router)
