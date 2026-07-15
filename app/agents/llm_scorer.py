"""
app/agents/llm_scorer.py
------------------------
The real LLM scoring layer for the lead agent.

This module owns the *model* side of lead scoring:
  1. Load the SYSTEM prompt from a file (never inlined in Python).
  2. Ask an OpenAI-compatible endpoint (OpenRouter) for a STRICT JSON verdict.
  3. Parse it via resilience.parse_llm_json_response.
  4. On a parse failure, re-prompt ONCE with the exact error for self-correction.
  5. On a second failure / timeout / HTTP error / LLM disabled, fall back to the
     deterministic rule-based DecisionEngine.

The graph node (node_lead_score) is responsible for applying the DecisionEngine
as a *guardrail* on top of this result — see graph.py. Keeping the two concerns
separate keeps this module a pure "call the model and give me structured output"
function that is trivial to unit test with a mocked completion.
"""
import hashlib
import json
import os
from functools import lru_cache
from typing import Dict, List, Literal, cast

from loguru import logger
from pydantic import BaseModel, Field, PrivateAttr

from app.core.config import settings
from app.agents.decision_engine import DecisionEngine, LeadContext
from app.core.resilience import call_llm_completion, parse_llm_json_response

# Closed value sets, shared by the model annotations and the runtime guards below.
Priority = Literal["HIGH", "MEDIUM", "LOW", "SPAM"]
Source = Literal["llm", "llm_selfcorrected", "rules_fallback"]

# Only these four values are valid priorities everywhere downstream.
_VALID_PRIORITIES = {"HIGH", "MEDIUM", "LOW", "SPAM"}

# Confidence assigned to any rule-based fallback. Deliberately BELOW the
# configured confidence gate (policy.decision.confidence_gate; see
# route_after_decision) so that a pure fallback ALWAYS routes to human
# escalation — we never let a silent model outage auto-action a lead.
FALLBACK_CONFIDENCE = 0.50


class ScoringResult(BaseModel):
    """Structured output of the scoring step. `source` records how we got here."""
    priority: Priority
    confidence: float = Field(ge=0.0, le=1.0)
    next_action: str
    reasoning: List[str] = Field(default_factory=list)
    source: Source

    # Observability only — NOT part of the scored-output contract, so it lives as
    # a Pydantic private attribute (excluded from serialization / schema). The
    # graph node reads it to build the "llm_score_lead" ToolCallRecord.
    _exchange: dict = PrivateAttr(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT LOADING (from file — never inlined)
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _read_prompt_cached(path: str, mtime: float) -> str:
    """
    Reads the system prompt from disk, cached on (path, mtime). Because mtime is
    part of the cache key, editing the file yields a new key → the next call
    re-reads it. This gives live prompt reloads WITHOUT a process restart.
    """
    with open(path, "r", encoding="utf-8") as fh:
        content = fh.read().strip()
    if not content:
        raise RuntimeError(f"Agent system prompt at '{path}' is empty.")
    return content


def get_system_prompt() -> str:
    """
    Loads the system prompt at call time. The prompt is NEVER inlined in Python;
    it lives at settings.AGENT_PROMPT_PATH and is hot-reloaded on edit (mtime).
    """
    path = settings.AGENT_PROMPT_PATH
    try:
        mtime = os.path.getmtime(path)
    except OSError as e:
        # Fail loudly rather than silently inlining a default prompt.
        raise RuntimeError(
            f"Agent system prompt not found at '{path}'. "
            "Set AGENT_PROMPT_PATH or create the file."
        ) from e
    return _read_prompt_cached(path, mtime)


# ─────────────────────────────────────────────────────────────
# LOW-LEVEL COMPLETION (single seam for tests to patch)
# ─────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_client():
    """Builds the OpenAI-compatible client pointed at OpenRouter (cached)."""
    from openai import OpenAI
    return OpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.resolved_llm_api_key.get_secret_value(),
    )


def _raw_completion(messages: List[Dict[str, str]]) -> str:
    """
    Returns the raw assistant text for `messages`. This is the ONE network seam;
    unit tests monkeypatch this function so nothing touches the wire.
    """
    return call_llm_completion(
        client=_get_client(),
        model=settings.LLM_MODEL,
        messages=messages,
        timeout_seconds=settings.LLM_TIMEOUT_SECONDS,
        use_json_format=True,
    )


# ─────────────────────────────────────────────────────────────
# PROMPT + PARSING HELPERS
# ─────────────────────────────────────────────────────────────

def _build_user_message(context: dict) -> str:
    """Serializes the lead context into the user turn."""
    return (
        "Score the following lead. Respond with ONLY a single JSON object "
        "matching the schema in your instructions.\n\n"
        f"LEAD CONTEXT:\n{json.dumps(context, indent=2, default=str)}"
    )


def _maybe_corrupt(raw: str) -> str:
    """
    Demo knob: when LLM_FORCE_MALFORMED is set, mangle the FIRST raw response so
    the self-correction path is exercised on camera. Inert by default.
    """
    if not settings.LLM_FORCE_MALFORMED:
        return raw
    logger.warning("[LLM] LLM_FORCE_MALFORMED active — corrupting first response for demo.")
    # Prepend prose and drop the final character → guaranteed invalid JSON.
    return "Sure, here is the JSON you asked for:\n" + raw[:-1]


def _to_result(parsed: dict, source: str) -> ScoringResult:
    """
    Coerces a parsed model dict into a validated ScoringResult. Raises ValueError
    if the model returned an unusable priority (treated as a parse-level failure
    by the caller → triggers fallback).
    """
    priority = str(parsed.get("priority", "")).upper()
    if priority not in _VALID_PRIORITIES:
        raise ValueError(f"LLM returned invalid priority: {parsed.get('priority')!r}")

    confidence = float(parsed.get("confidence", 0.0))
    confidence = max(0.0, min(1.0, confidence))  # clamp defensively

    # Demo-only, env-guarded override: force a deterministic confidence so the
    # low-confidence → escalation path is reproducible even when the live model
    # is over-confident. Inert unless LLM_FORCE_CONFIDENCE is set AND we're in
    # the development environment.
    if settings.LLM_FORCE_CONFIDENCE is not None and settings.ENVIRONMENT == "development":
        confidence = max(0.0, min(1.0, float(settings.LLM_FORCE_CONFIDENCE)))
        logger.warning(f"[LLM] LLM_FORCE_CONFIDENCE active — overriding confidence to {confidence}.")

    reasoning = parsed.get("reasoning") or []
    if isinstance(reasoning, str):
        reasoning = [reasoning]

    # `priority` was validated against _VALID_PRIORITIES above; `source` is
    # supplied by our own callers. cast() narrows str → Literal for the type
    # checkers without any runtime effect.
    return ScoringResult(
        priority=cast(Priority, priority),
        confidence=confidence,
        next_action=str(parsed.get("next_action", "notify")),
        reasoning=[str(r) for r in reasoning],
        source=cast(Source, source),
    )


# ─────────────────────────────────────────────────────────────
# DETERMINISTIC FALLBACK
# ─────────────────────────────────────────────────────────────

def _rules_fallback(context: dict, reason: str) -> ScoringResult:
    """
    Deterministic degrade path: run the rule engine, stamp confidence at 0.50
    (below the confidence gate → downstream escalation), source="rules_fallback".
    """
    logger.warning(f"[LLM] Falling back to rule-based scoring. Reason: {reason}")
    # Compute the rule-based PRIORITY as if confident (ai_confidence=1.0) so the
    # engine's LowConfidenceRule doesn't short-circuit and mask the real signal
    # (e.g. a freemail lead should still read as LOW). We then OVERWRITE confidence
    # with FALLBACK_CONFIDENCE (0.50) on the result, which is below the confidence
    # gate — so a pure fallback always routes to human escalation downstream.
    lead_ctx = LeadContext(
        email=context.get("email"),
        budget=context.get("budget", 0.0) or 0.0,
        job_title=context.get("job_title", "") or "",
        ai_confidence=1.0,
        company_size=context.get("company_size"),
        is_freemail=context.get("is_freemail", False),
    )
    decision = DecisionEngine().process_lead(lead_ctx)
    result = ScoringResult(
        priority=cast(Priority, decision.priority if decision.priority in _VALID_PRIORITIES else "MEDIUM"),
        confidence=FALLBACK_CONFIDENCE,
        next_action=decision.action,
        reasoning=[f"Rule-based fallback ({reason})."] + decision.reasoning,
        source="rules_fallback",
    )
    result._exchange = {
        "source": "rules_fallback",
        "fallback_reason": reason,
        "raw_response": None,
        "parsed": None,
    }
    return result


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINT
# ─────────────────────────────────────────────────────────────

def score_lead(context: dict) -> ScoringResult:
    """
    Score a single lead with the LLM, self-correcting once on malformed JSON and
    degrading to the rule engine on any hard failure.

    See module docstring for the full call sequence. The returned result carries
    an observability payload on `result._exchange` for the graph's audit trail.
    """
    if not settings.LLM_ENABLED:
        return _rules_fallback(context, "LLM_ENABLED=false")

    system_prompt = get_system_prompt()
    user_message = _build_user_message(context)
    prompt_excerpt = user_message[:500]
    prompt_hash = hashlib.sha256(
        (system_prompt + user_message).encode("utf-8")
    ).hexdigest()

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # ── Attempt 1 ────────────────────────────────────────────
    try:
        raw = _raw_completion(messages)
    except Exception as e:  # network error, breaker open, retries exhausted, etc.
        return _rules_fallback(context, f"llm_call_failed: {type(e).__name__}: {e}")

    raw = _maybe_corrupt(raw)  # demo knob; no-op unless forced
    first_parse = parse_llm_json_response(raw)

    if first_parse["parse_error"] is None:
        try:
            result = _to_result(first_parse["data"], source="llm")
        except (ValueError, TypeError) as e:
            return _rules_fallback(context, f"invalid_llm_payload: {e}")
        result._exchange = {
            "source": "llm",
            "prompt_hash": prompt_hash,
            "prompt_excerpt": prompt_excerpt,
            "raw_response": raw,
            "parsed": first_parse["data"],
        }
        return result

    # ── Attempt 2: self-correction re-prompt ─────────────────
    logger.warning(f"[LLM] First response failed to parse: {first_parse['parse_error']}. Re-prompting.")
    correction = (
        "Your previous response could not be parsed as JSON.\n"
        f"Parse error: {first_parse['parse_error']}\n"
        f"Your previous output was:\n{raw}\n\n"
        "Return ONLY the corrected, valid JSON object — no prose, no code fences."
    )
    messages.append({"role": "assistant", "content": raw})
    messages.append({"role": "user", "content": correction})

    try:
        raw2 = _raw_completion(messages)
    except Exception as e:
        return _rules_fallback(context, f"llm_recall_failed: {type(e).__name__}: {e}")

    second_parse = parse_llm_json_response(raw2)
    if second_parse["parse_error"] is not None:
        return _rules_fallback(context, f"json_parse_failed_twice: {second_parse['parse_error']}")

    try:
        result = _to_result(second_parse["data"], source="llm_selfcorrected")
    except (ValueError, TypeError) as e:
        return _rules_fallback(context, f"invalid_llm_payload_after_correction: {e}")

    result._exchange = {
        "source": "llm_selfcorrected",
        "prompt_hash": prompt_hash,
        "prompt_excerpt": prompt_excerpt,
        "raw_response": raw2,
        "raw_response_first": raw,
        "parsed": second_parse["data"],
    }
    return result
