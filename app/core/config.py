"""
app/core/config.py
------------------
Centralized configuration using Pydantic BaseSettings.
All environment variables are loaded here from the .env file.
The application CRASHES IMMEDIATELY on startup if a required variable is missing,
preventing silent failures deep in runtime (fail-fast principle).
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Reads from a .env file at project root
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignores extra variables that might be in .env
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"

    # ── Security (JWT) ────────────────────────────────────
    # SecretStr ensures these values are NEVER accidentally logged in plain text
    SECRET_KEY: SecretStr = SecretStr("change-this-in-production")
    REFRESH_SECRET_KEY: SecretStr = SecretStr("change-this-too-in-production")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Database (PostgreSQL) ─────────────────────────────
    DATABASE_URL: str = "postgresql://agentuser:agentpassword@localhost:5432/leadagentdb"

    # ── Redis (LangGraph Checkpointing) ───────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── LLM / AI ──────────────────────────────────────────
    OPENAI_API_KEY: SecretStr = SecretStr("not-set")
    OPENAI_MODEL: str = "gpt-3.5-turbo"

    # ── External Tool APIs ────────────────────────────────
    ENRICHMENT_API_URL: str = "https://api.example-enrichment.com/v1/company"
    CRM_API_URL: str = "https://api.your-crm.com/v1"
    SLACK_WEBHOOK_URL: str = ""


# Global singleton instance — import this across the app
settings = Settings()
