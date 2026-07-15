"""
app/core/logging_config.py
---------------------------
Configures Loguru for structured JSON logging.

Key design decisions:
  1. JSON output (serialize=True) — instantly queryable by Datadog / ELK / CloudWatch.
  2. ContextVar injection — the workflow_id is automatically appended to EVERY log
     line without passing it through function arguments.
  3. backtrace=True — full stack traces on exceptions for production debugging.
  4. diagnose=False — hides local variable values in stack traces to prevent
     accidental PII or secret key leakage in production logs.
"""
import sys
from contextvars import ContextVar

from loguru import logger


# ── Correlation ID Context ─────────────────────────────────
# This ContextVar tracks the current workflow_id.
# In async Python, each coroutine gets its own copy, so two concurrent
# leads being processed simultaneously won't mix up their correlation IDs.
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="SYSTEM")


def _inject_correlation_id(record: dict) -> None:
    """
    Loguru patch function called before every log write.
    Reads the current ContextVar and injects it into the log record's extra dict.
    """
    record["extra"]["correlation_id"] = correlation_id_ctx.get()


def setup_logging() -> None:
    """
    Call this once at application startup (in app/main.py lifespan).
    Removes Loguru's default text handler and replaces it with a JSON handler.
    """
    # Remove the default plain-text handler
    logger.remove()

    # Make the log stream encoding-safe. Loguru serializes JSON with
    # ensure_ascii=False, so records containing non-ASCII (level icons, ₹, names)
    # would raise UnicodeEncodeError on a legacy console (e.g. Windows cp1252) and
    # spam "Logging error in Handler". UTF-8 with a backslash fallback prevents that
    # on every platform; production (Linux/Docker) is already UTF-8.
    _reconfigure = getattr(sys.stdout, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):  # non-reconfigurable stream (rare)
            pass

    # Add structured JSON handler to stdout
    logger.add(
        sys.stdout,
        serialize=True,       # Output as JSON — the key setting for production observability
        level="INFO",
        enqueue=True,         # Makes logging thread-safe and non-blocking
        backtrace=True,       # Include full tracebacks on errors
        diagnose=False,       # Do NOT expose local variable values (security best practice)
        colorize=False,       # No ANSI color codes in JSON output
    )

    # Register the correlation ID injector — runs before EVERY log write
    logger.configure(patcher=_inject_correlation_id)

    logger.info("Structured JSON logging initialized.")


def set_correlation_id(workflow_id: str) -> None:
    """
    Sets the workflow_id as the correlation ID for the current async context.
    Call this at the start of every LangGraph workflow execution.
    All subsequent log calls in the same async context will include this ID.
    """
    correlation_id_ctx.set(workflow_id)
