"""Application settings loaded from environment variables and .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the Smart Deck API."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "smart-deck-api"
    debug: bool = False
    cors_origins: list[str] = ["http://localhost:5173"]


settings = Settings()
