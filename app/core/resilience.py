"""
app/core/resilience.py
-----------------------
Centralized retry logic, circuit breakers, and graceful degradation strategies.

Patterns used:
  1. Exponential Backoff (Tenacity) — for transient API failures (CRM, Enrichment)
  2. Circuit Breaker (PyBreaker) — protects struggling services from being overwhelmed
  3. Graceful Degradation — returns partial/empty data instead of crashing the workflow
  4. Escalation — explicit handoff to human when all retries are exhausted

Failure scenarios handled:
  - CRM timeout
  - Database unavailable
  - LLM malformed JSON
  - Network error during enrichment
  - Missing required lead fields
"""
import json
import logging
import requests
from typing import Optional, Dict, Any

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryError,
)
from pybreaker import CircuitBreaker, CircuitBreakerError
from loguru import logger

from app.core.config import settings


# ─────────────────────────────────────────────────────────────
# CIRCUIT BREAKERS
# fail_max: number of failures before the breaker OPENS
# reset_timeout: seconds to wait before trying again (half-open state)
# ─────────────────────────────────────────────────────────────

# Breaker tuning comes from config/policy.yaml (resilience.*_breaker). See
# app/core/policy.py. Read once at import — breakers are process-global objects.
from app.core.policy import get_policy

_policy = get_policy()

# CRM circuit breaker — more tolerant.
crm_breaker = CircuitBreaker(
    fail_max=_policy.resilience.crm_breaker.fail_max,
    reset_timeout=_policy.resilience.crm_breaker.reset_timeout,
)

# Enrichment API circuit breaker — moderately tolerant.
enrichment_breaker = CircuitBreaker(
    fail_max=_policy.resilience.enrichment_breaker.fail_max,
    reset_timeout=_policy.resilience.enrichment_breaker.reset_timeout,
)

# Database circuit breaker — infrastructure protection (not a business constant,
# so intentionally not in policy.yaml). Fail fast to protect connection pools.
db_breaker = CircuitBreaker(fail_max=3, reset_timeout=30)

# LLM circuit breaker — protects us (and our spend) when the model endpoint is unhealthy.
llm_breaker = CircuitBreaker(
    fail_max=_policy.resilience.llm_breaker.fail_max,
    reset_timeout=_policy.resilience.llm_breaker.reset_timeout,
)


# ─────────────────────────────────────────────────────────────
# SCENARIO 1: CRM Timeout
# Strategy: Exponential Backoff + Circuit Breaker + Escalation
# ─────────────────────────────────────────────────────────────

@crm_breaker
@retry(
    stop=stop_after_attempt(4),
    # Waits: 2s → 4s → 8s between attempts
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError)),
    reraise=True,
)
def update_crm(lead_id: str, payload: Dict[str, Any]) -> Dict:
    """
    Pushes the lead's final classification and status to the CRM.
    Decorated with both retry logic and a circuit breaker.
    
    If the CRM is consistently failing, the circuit breaker opens and future
    calls fail immediately (without waiting for timeout), protecting the CRM.
    """
    logger.info(f"Attempting CRM update for lead {lead_id}...")
    url = f"{settings.CRM_API_URL}/leads/{lead_id}"
    # Strict timeout (policy.resilience.crm_timeout_seconds) — never let network
    # calls block indefinitely.
    response = requests.post(url, json=payload, timeout=get_policy().resilience.crm_timeout_seconds)
    response.raise_for_status()
    return response.json()


def safe_update_crm(lead_id: str, payload: Dict[str, Any]) -> str:
    """
    Wraps update_crm with fallback handling.
    Returns a status string the LangGraph node can use for routing decisions.
    """
    try:
        update_crm(lead_id, payload)
        return "SUCCESS"
    except CircuitBreakerError:
        # The CRM breaker is open — too many recent failures.
        # Route to Dead Letter Queue (DLQ) for replay when CRM recovers.
        logger.error(f"CRM circuit breaker OPEN for lead {lead_id}. Queuing for DLQ.")
        return "ESCALATE_TO_QUEUE"
    except RetryError:
        # All 4 retry attempts failed — escalate to human
        logger.error(f"CRM update failed after all retries for lead {lead_id}. Escalating.")
        return "ESCALATE_TO_HUMAN"
    except Exception as e:
        logger.error(f"Unexpected CRM error for lead {lead_id}: {e}")
        return "ESCALATE_TO_HUMAN"


# ─────────────────────────────────────────────────────────────
# SCENARIO 2: Enrichment API Failure
# Strategy: Exponential Backoff + Graceful Degradation
# ─────────────────────────────────────────────────────────────

@enrichment_breaker
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((requests.exceptions.Timeout, requests.exceptions.ConnectionError)),
    reraise=True,
)
def call_enrichment_api(domain: str) -> Dict:
    """
    Fetches company data (name, industry, size) based on a domain.
    Retried up to 3 times with exponential backoff.
    """
    logger.info(f"Calling enrichment API for domain: {domain}")
    url = f"{settings.ENRICHMENT_API_URL}?domain={domain}"
    response = requests.get(url, timeout=get_policy().resilience.enrichment_timeout_seconds)
    response.raise_for_status()
    return response.json()


def safe_enrich_domain(domain: str) -> Dict:
    """
    Graceful degradation: if the enrichment API is down, we return empty
    fields so the agent can still process the lead with available data.
    The LLM will detect the null values and factor them into its scoring.
    """
    try:
        return call_enrichment_api(domain)
    except (RetryError, CircuitBreakerError, Exception) as e:
        logger.warning(f"Enrichment API failed for {domain} — degrading gracefully: {e}")
        # Return a valid but empty response — the workflow CONTINUES
        return {
            "company_name": None,
            "industry": None,
            "company_size": None,
            "enrichment_failed": True,  # Flag for the LLM to notice
        }


# ─────────────────────────────────────────────────────────────
# SCENARIO 2b: LLM Chat Completion
# Strategy: Exponential Backoff (network errors only) + Circuit Breaker
# Mirrors the enrichment pattern: a decorated low-level call that the caller
# wraps with its own fallback (see app/agents/llm_scorer.py).
# ─────────────────────────────────────────────────────────────

# openai exposes typed network errors; import lazily-safe at module load since
# openai is a hard dependency of the LLM scoring path.
try:
    from openai import APIConnectionError, APITimeoutError
    # Retry ONLY on genuine network/transport failures — never on 4xx/auth/etc.,
    # which are deterministic and would just waste attempts + spend.
    LLM_RETRYABLE_ERRORS = (APIConnectionError, APITimeoutError)
except ImportError:  # pragma: no cover - openai should be installed
    LLM_RETRYABLE_ERRORS = (requests.exceptions.Timeout, requests.exceptions.ConnectionError)


@llm_breaker
@retry(
    stop=stop_after_attempt(3),   # 1 initial attempt + 2 retries
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(LLM_RETRYABLE_ERRORS),
    reraise=True,
)
def call_llm_completion(client, model: str, messages: list, timeout_seconds: int,
                        use_json_format: bool = True) -> str:
    """
    Executes a single chat completion against an OpenAI-compatible endpoint and
    returns the assistant message content as a raw string.

    Resilience: retried up to twice on network errors (exponential backoff) and
    guarded by llm_breaker. Deterministic errors (bad request, auth, rate-limit
    payloads that raise non-network errors) propagate immediately so the caller
    can fall back rather than hammer the endpoint.
    """
    kwargs: Dict[str, Any] = {"model": model, "messages": messages, "timeout": timeout_seconds}
    if use_json_format:
        # Most OpenRouter instruct models honor this; if one doesn't, the request
        # errors and the caller degrades to the rule-based fallback.
        kwargs["response_format"] = {"type": "json_object"}

    logger.info(f"Calling LLM: model={model}, json_mode={use_json_format}")
    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content or ""


# ─────────────────────────────────────────────────────────────
# SCENARIO 3: LLM Malformed JSON
# Strategy: Re-prompt with the exact error message (self-correction)
# ─────────────────────────────────────────────────────────────

def _strip_code_fence(text: str) -> str:
    """
    Removes a surrounding Markdown code fence (```json ... ``` or ``` ... ```)
    if present, returning the inner payload.

    BUGFIX: the previous implementation used ``.strip("```json")``, which treats
    its argument as a SET OF CHARACTERS ({'`','j','s','o','n'}) and greedily eats
    any of those characters from both ends. That silently corrupts valid payloads
    whose boundary characters fall in that set — e.g. a bare ``null`` becomes
    ``ull``, and content adjacent to the fence can lose leading letters. We now
    remove the fence tokens as exact substrings (prefix/suffix), so keys and
    values like ``json_data`` survive untouched.
    """
    stripped = text.strip()

    # Opening fence: ``` optionally followed by a language tag on the same line.
    if stripped.startswith("```"):
        stripped = stripped[3:]
        # Drop an optional leading language tag (json / JSON) — as a substring,
        # NOT a character set.
        for tag in ("json", "JSON"):
            if stripped.startswith(tag):
                stripped = stripped[len(tag):]
                break
        stripped = stripped.lstrip("\n").lstrip()

    # Closing fence.
    if stripped.endswith("```"):
        stripped = stripped[:-3]

    return stripped.strip()


def parse_llm_json_response(raw_response: str, retry_count: int = 0) -> Dict:
    """
    Parses a JSON response from the LLM.

    If the JSON is malformed (LLM hallucinated bad formatting), the error
    message is returned so the caller can inject it back into the next LLM prompt,
    allowing the model to self-correct.

    Returns a dict with either 'data' or 'parse_error'.
    """
    try:
        # Strip common LLM artifacts like markdown code fences (see _strip_code_fence).
        cleaned = _strip_code_fence(raw_response)
        parsed = json.loads(cleaned)
        return {"data": parsed, "parse_error": None}
    except json.JSONDecodeError as e:
        logger.warning(
            f"LLM JSON parse failed (attempt {retry_count + 1}): {e}. "
            "Will inject error into next prompt for self-correction."
        )
        return {
            "data": None,
            "parse_error": str(e),  # This exact error string goes back into the LLM prompt
        }


# ─────────────────────────────────────────────────────────────
# SCENARIO 4: Missing Fields
# Strategy: Explicit business-logic escalation (not a retry)
# ─────────────────────────────────────────────────────────────

REQUIRED_FIELDS = ["email"]  # Minimum required for lead processing

def validate_required_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates that minimum required fields are present.
    Missing data is a BUSINESS ERROR, not a technical one — we don't retry.
    Instead, we return a structured result for the LangGraph routing edge.
    
    Returns {'valid': True} or {'valid': False, 'missing': [...]}
    """
    missing = [f for f in REQUIRED_FIELDS if not payload.get(f)]

    if missing:
        logger.info(f"Lead is missing required fields: {missing}. Routing to escalation.")
        return {"valid": False, "missing": missing}

    return {"valid": True, "missing": []}
