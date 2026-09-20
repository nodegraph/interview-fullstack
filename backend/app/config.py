from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str
    cors_origins: str = "http://localhost:5173"

    # RxNorm / RxNav public REST API (used as a fallback when a medication is
    # not already in the local reference catalog). No auth required.
    rxnav_base_url: str = "https://rxnav.nlm.nih.gov/REST"

    # LLM configuration for the take-home medication-detection task.
    # Required for analysis; the rest of the app works without a key.
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
