"""Application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Find .env by walking up from config.py until we find it (works in both
# local dev: apps/backend/src/config.py → root/.env  and
# Docker: /app/src/config.py → /app/.env or env vars only)
def _find_env_file() -> str:
    p = Path(__file__).resolve().parent
    for _ in range(6):  # up to 6 levels
        candidate = p / ".env"
        if candidate.is_file():
            return str(candidate)
        p = p.parent
    return ".env"  # fallback — pydantic-settings will just use OS env vars


_ENV_FILE = _find_env_file()


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
    MAX_CONCURRENT_LLM_CALLS: int = 10

    # --- Debug ---
    DEBUG: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    return Settings()
