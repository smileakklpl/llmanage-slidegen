"""Application settings loaded from environment variables and .env file."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Smart Deck API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "smart-deck-api"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]

    # All durable inputs, outputs, stage dumps, and job states live in S3.
    s3_bucket: str = ""
    aws_region: str = "us-west-2"
    s3_endpoint_url: str | None = None
    s3_presign_expires_seconds: int = Field(default=3600, ge=60, le=604800)

    # Local/demo routing can use deterministic fake LLM responses. Production
    # leaves this false and follows the per-stage LLM environment settings.
    generation_use_fake_llm: bool = False
    generation_skip_semantic_review: bool = False
    generation_policy: str = "required"
    # End-to-end SLA starts when the job is persisted and 202 is returned.
    generation_deadline_seconds: float = Field(default=1500.0, gt=0)
    generation_render_reserve_seconds: float = Field(default=240.0, ge=0)
    generation_output_reserve_seconds: float = Field(default=150.0, ge=0)
    generation_max_concurrent_jobs: int = Field(default=1, ge=1)

    # OCR is a soft budget. Completed trusted pages are retained on cutoff.
    ocr_max_seconds: float = Field(default=360.0, gt=0)
    ocr_max_pages: int = Field(default=20, ge=1)


settings = Settings()
