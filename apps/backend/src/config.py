"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env lives at the project root: Take_Home_Dentist/.env
# config.py is at apps/backend/src/config.py → parents: src(0) → backend(1) → apps(2) → root(3)
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Central settings object — values come from .env or OS env vars."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Gemini LLM ---
    GEMINI_API_KEY: str

    # --- Database ---
    DATABASE_URL: str

    # --- Redis ---
    REDIS_URL: str

    # --- ChromaDB ---
    CHROMA_PERSIST_DIR: str

    # --- JWT Auth ---
    JWT_SECRET_KEY: str

    # --- Concurrency ---
    MAX_CONCURRENT_LLM_CALLS: int

    # --- Debug ---
    DEBUG: bool


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    return Settings()
