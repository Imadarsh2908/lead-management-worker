"""
app/utils/audit.py
-------------------
Helper functions for emitting structured, typed log events.

Why use wrapper functions instead of calling logger.info() directly?
  - Enforces a CONSISTENT JSON schema for every event type.
  - Prevents developers from writing unstructured messages like logger.info("done").
  - Makes log parsing trivial in Datadog/ELK: filter by event_type field directly.
  - Correlation IDs are injected automatically via ContextVar (no manual passing needed).
"""
from typing import Any, Dict, Optional

from loguru import logger


def log_api_request(method: str, url: str, status_code: int, latency_ms: int) -> None:
    """Logs an inbound or outbound API request."""
    logger.bind(
        event_type="api_request",
        method=method,
        url=url,
        status_code=status_code,
        latency_ms=latency_ms,
    ).info(f"API {method} {url} → {status_code} ({latency_ms}ms)")


def log_tool_execution(
    tool_name: str,
    inputs: Dict[str, Any],
    outputs: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Logs a tool invocation with its inputs, outputs, and success/failure state."""
    success = error is None
    log_fn = logger.info if success else logger.error

    log_fn(
        f"Tool '{tool_name}' {'succeeded' if success else 'FAILED'}",
        event_type="tool_execution",
        tool_name=tool_name,
        inputs=inputs,
        outputs=outputs,
        error=error,
        success=success,
    )


def log_llm_call(
    model: str,
    tokens_used: int,
    latency_ms: int,
    prompt_summary: str,
) -> None:
    """Logs an LLM API call with token usage for cost monitoring."""
    logger.bind(
        event_type="llm_call",
        model=model,
        tokens_used=tokens_used,
        latency_ms=latency_ms,
    ).info(f"LLM call to '{model}': {prompt_summary}")


def log_workflow_state(step: str, status: str, retry_count: int = 0) -> None:
    """Logs a workflow state transition."""
    logger.bind(
        event_type="workflow_state",
        step=step,
        status=status,
        retry_count=retry_count,
    ).info(f"Workflow transitioned → step='{step}' status='{status}'")


def log_escalation(reason: str, confidence: float) -> None:
    """Logs an escalation event with the reason and AI confidence score."""
    logger.bind(
        event_type="escalation",
        reason=reason,
        confidence=confidence,
    ).warning(f"ESCALATION triggered: {reason} (confidence={confidence:.0%})")


def log_user_created(admin_username: str, new_username: str, role: str) -> None:
    """Logs an Admin creating a new user account."""
    logger.bind(
        event_type="user_created",
        admin=admin_username,
        new_user=new_username,
        role=role,
    ).info(f"User account CREATED: '{new_username}' (role={role}) by admin '{admin_username}'")


def log_user_access_revoked(admin_username: str, target_username: str) -> None:
    """Logs an Admin revoking another user's access."""
    logger.bind(
        event_type="user_access_revoked",
        admin=admin_username,
        target_user=target_username,
    ).warning(f"User access REVOKED: '{target_username}' by admin '{admin_username}'")


def log_user_access_restored(admin_username: str, target_username: str) -> None:
    """Logs an Admin restoring a previously revoked user's access."""
    logger.bind(
        event_type="user_access_restored",
        admin=admin_username,
        target_user=target_username,
    ).info(f"User access RESTORED: '{target_username}' by admin '{admin_username}'")
