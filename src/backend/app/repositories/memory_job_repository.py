"""In-memory implementation of JobRepository for development/testing.

Data is stored in a plain dict keyed by job_id. No persistence — data is
lost on process restart. This is intentional for Phase 1.
"""

from app.repositories.job_repository import JobModel, JobRepository


class MemoryJobRepository(JobRepository):
    """In-memory implementation of JobRepository for development/testing."""

    def __init__(self) -> None:
        self._store: dict[str, JobModel] = {}

    async def create(self, job: JobModel) -> JobModel:
        """Persist a new job in the in-memory store."""
        self._store[job.job_id] = job
        return job

    async def get(self, job_id: str) -> JobModel | None:
        """Retrieve a job by its ID, or None if not found."""
        return self._store.get(job_id)

    async def update(self, job: JobModel) -> JobModel:
        """Update an existing job in the in-memory store."""
        self._store[job.job_id] = job
        return job
