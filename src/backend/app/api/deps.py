"""Dependency injection helpers for API routes.

Provides singleton instances of services/repositories so all endpoints
share the same in-memory state.
"""

from app.repositories.memory_job_repository import MemoryJobRepository
from app.services.job_service import JobService

# Singleton instances (shared across the process lifetime).
_repository = MemoryJobRepository()
_job_service = JobService(repository=_repository)


def get_job_service() -> JobService:
    """Return the global JobService instance."""
    return _job_service
