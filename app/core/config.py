"""
app/core/config.py
------------------
Centralized configuration using Pydantic BaseSettings.
All environment variables are loaded here from the .env file.
The application CRASHES IMMEDIATELY on startup if a required variable is missing,
preventing silent failures deep in runtime (fail-fast principle).
"""
from typing import Optional

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
    # OpenAI-compatible client pointed at OpenRouter (open-source-first: the
    # target org runs open-weight models). OpenRouter speaks the OpenAI Chat
    # Completions API, so the official `openai` SDK works unchanged.
    LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
    # Inexpensive open-weight instruct model currently available on OpenRouter.
    # (Qwen2.5 7B Instruct — cheap, supports JSON response_format. Swap freely,
    #  e.g. "meta-llama/llama-3.1-8b-instruct".)
    LLM_MODEL: str = "qwen/qwen-2.5-7b-instruct"
    LLM_API_KEY: SecretStr = SecretStr("not-set")
    LLM_TIMEOUT_SECONDS: int = 20
    LLM_ENABLED: bool = True
    # Path to the SYSTEM prompt file. Loaded at runtime — the prompt is NEVER
    # inlined as a Python string. A later phase expands AGENTS.md.
    AGENT_PROMPT_PATH: str = "AGENTS.md"
    # Demo-only forcing knob: when true, llm_scorer corrupts its FIRST raw
    # response so the self-correction / fallback path can be filmed. Inert
    # unless explicitly set to true.
    LLM_FORCE_MALFORMED: bool = False
    # Demo-only forcing knob: override the LLM's returned confidence with this
    # value (e.g. 0.40) so the low-confidence → escalation path is reproducible
    # even when the live model is over-confident. Inert unless set AND
    # ENVIRONMENT == "development" (see llm_scorer._to_result).
    LLM_FORCE_CONFIDENCE: Optional[float] = None

    # ── DEPRECATED: legacy OpenAI vars ────────────────────
    # Kept only for backward compatibility. Prefer LLM_* above. If LLM_API_KEY
    # is left unset we transparently fall back to OPENAI_API_KEY (see
    # resolved_llm_api_key). These will be removed in a future release.
    OPENAI_API_KEY: SecretStr = SecretStr("not-set")
    OPENAI_MODEL: str = "gpt-3.5-turbo"

    @property
    def resolved_llm_api_key(self) -> SecretStr:
        """
        The API key to use for the LLM client. Prefers LLM_API_KEY, falling
        back to the deprecated OPENAI_API_KEY for backward compatibility.
        """
        if self.LLM_API_KEY.get_secret_value() not in ("", "not-set"):
            return self.LLM_API_KEY
        return self.OPENAI_API_KEY

    # ── External Tool APIs ────────────────────────────────
    ENRICHMENT_API_URL: str = "https://api.example-enrichment.com/v1/company"
    CRM_API_URL: str = "https://api.your-crm.com/v1"
    SLACK_WEBHOOK_URL: str = ""

    # ── Email (SMTP) ──────────────────────────────────────
    # When EMAIL_ENABLED is False the mailer runs in DRY-RUN mode: it logs the
    # message it *would* send instead of opening an SMTP connection. This keeps
    # local/dev/demo runs working with zero mail config — flip it on and fill in
    # the SMTP_* values (e.g. a Gmail App Password) to send for real.
    EMAIL_ENABLED: bool = False
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr = SecretStr("")
    SMTP_USE_TLS: bool = True   # STARTTLS on 587; set False + port 465 for implicit SSL
    EMAIL_FROM: str = "Lead Flow AI <no-reply@example.com>"

    # ── Lead import (bulk / inbound email) ────────────────
    # Max rows accepted in a single file/paste import (guards against a huge
    # upload spawning thousands of background workflows at once).
    MAX_IMPORT_ROWS: int = 500
    # Shared secret for the public inbound-email webhook. The webhook is INERT
    # (returns 404) unless this is set AND the request presents the matching
    # token — so an unconfigured deployment exposes no open lead-injection hole.
    INBOUND_EMAIL_TOKEN: SecretStr = SecretStr("")

    # ── Scheduler (APScheduler) ───────────────────────────
    # The background scheduler polls for due scheduled emails and dispatches
    # them. Disabled automatically in the test environment (see scheduler.py).
    SCHEDULER_ENABLED: bool = True
    # How often (seconds) to check for emails whose scheduled_at time has passed.
    EMAIL_DISPATCH_INTERVAL_SECONDS: int = 60
    # Max delivery attempts before a scheduled email is marked FAILED.
    EMAIL_MAX_ATTEMPTS: int = 3


# Global singleton instance — import this across the app
settings = Settings()
