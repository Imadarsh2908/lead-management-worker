"""
tests/agents/test_llm_scorer.py
-------------------------------
Unit tests for the real LLM scoring path. No network: the single network seam
(`llm_scorer._raw_completion`) is monkeypatched in every test.
"""
import json
import pytest

from app.core.config import settings
from app.agents import llm_scorer
from app.agents.llm_scorer import score_lead, ScoringResult, FALLBACK_CONFIDENCE


HIGH_VALUE_CONTEXT = {
    "email": "ceo@bigcorp.com",
    "budget": 900000,
    "job_title": "CEO",
    "company_size": "Enterprise",
    "is_freemail": False,
}

FREEMAIL_CONTEXT = {
    "email": "someone@gmail.com",
    "budget": 0,
    "job_title": "",
    "company_size": None,
    "is_freemail": True,
}


@pytest.fixture(autouse=True)
def _enable_llm(monkeypatch):
    """Default every test to LLM enabled, forcing knob off."""
    monkeypatch.setattr(settings, "LLM_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_FORCE_MALFORMED", False)


def _valid_payload(priority="HIGH", confidence=0.91):
    return json.dumps({
        "priority": priority,
        "confidence": confidence,
        "next_action": "generate_follow_up",
        "reasoning": ["High budget", "Senior decision maker"],
    })


def test_happy_path_valid_json(monkeypatch):
    """Valid JSON on the first try → source='llm', fields propagate."""
    monkeypatch.setattr(llm_scorer, "_raw_completion", lambda messages, model=None: _valid_payload("HIGH", 0.91))

    result = score_lead(HIGH_VALUE_CONTEXT)

    assert isinstance(result, ScoringResult)
    assert result.priority == "HIGH"
    assert result.confidence == 0.91
    assert result.source == "llm"
    assert result._exchange["raw_response"] is not None


def test_malformed_then_corrected(monkeypatch):
    """First response is broken JSON; the re-prompt returns valid → 'llm_selfcorrected'."""
    responses = iter([
        "Sure! here you go: {broken json, not parseable",   # attempt 1 — invalid
        _valid_payload("MEDIUM", 0.80),                       # attempt 2 — valid
    ])
    calls = []

    def fake(messages, model=None):
        calls.append(messages)
        return next(responses)

    monkeypatch.setattr(llm_scorer, "_raw_completion", fake)

    result = score_lead(HIGH_VALUE_CONTEXT)

    assert result.source == "llm_selfcorrected"
    assert result.priority == "MEDIUM"
    assert result.confidence == 0.80
    # Second call must include the correction turn referencing the parse error.
    assert len(calls) == 2
    correction_turn = calls[1][-1]["content"]
    assert "could not be parsed" in correction_turn


def test_double_failure_falls_back_to_rules(monkeypatch):
    """Malformed twice → deterministic rules_fallback at confidence 0.50."""
    monkeypatch.setattr(llm_scorer, "_raw_completion", lambda messages, model=None: "still not json {{{")

    result = score_lead(FREEMAIL_CONTEXT)

    assert result.source == "rules_fallback"
    assert result.confidence == FALLBACK_CONFIDENCE == 0.50
    # Rule engine downgrades freemail leads to LOW.
    assert result.priority == "LOW"


def test_network_error_falls_back(monkeypatch):
    """A transport error out of the call layer degrades to rules_fallback."""
    def boom(messages, model=None):
        raise ConnectionError("dns exploded")

    monkeypatch.setattr(llm_scorer, "_raw_completion", boom)

    result = score_lead(HIGH_VALUE_CONTEXT)
    assert result.source == "rules_fallback"
    assert result.confidence == 0.50


def test_llm_disabled_uses_fallback(monkeypatch):
    """LLM_ENABLED=false short-circuits straight to the rule engine."""
    monkeypatch.setattr(settings, "LLM_ENABLED", False)
    # Guard: the network seam must NOT be called when disabled.
    monkeypatch.setattr(llm_scorer, "_raw_completion",
                        lambda messages, model=None: pytest.fail("LLM called while disabled"))

    result = score_lead(HIGH_VALUE_CONTEXT)
    assert result.source == "rules_fallback"


def test_invalid_priority_falls_back(monkeypatch):
    """Well-formed JSON but a nonsense priority is treated as unusable → fallback."""
    payload = json.dumps({"priority": "SUPER", "confidence": 0.9, "next_action": "notify", "reasoning": []})
    monkeypatch.setattr(llm_scorer, "_raw_completion", lambda messages, model=None: payload)

    result = score_lead(HIGH_VALUE_CONTEXT)
    assert result.source == "rules_fallback"


def test_force_malformed_knob_triggers_selfcorrection(monkeypatch):
    """The demo knob corrupts the FIRST response, exercising self-correction."""
    monkeypatch.setattr(settings, "LLM_FORCE_MALFORMED", True)
    # Both underlying responses are valid; the knob mangles the first one.
    monkeypatch.setattr(llm_scorer, "_raw_completion", lambda messages, model=None: _valid_payload("HIGH", 0.9))

    result = score_lead(HIGH_VALUE_CONTEXT)
    assert result.source == "llm_selfcorrected"


def test_force_malformed_inert_by_default(monkeypatch):
    """With the knob off (default), a valid response parses first try."""
    monkeypatch.setattr(llm_scorer, "_raw_completion", lambda messages, model=None: _valid_payload("LOW", 0.75))
    result = score_lead(FREEMAIL_CONTEXT)
    assert result.source == "llm"
    assert result.priority == "LOW"


# ─────────────────────────────────────────────────────────────
# Model failover chain (LLM_MODEL -> LLM_FALLBACK_MODELS -> rules)
# ─────────────────────────────────────────────────────────────

def test_failover_primary_fails_secondary_succeeds(monkeypatch):
    """Primary model errors (e.g. 429) → transparently score with the next model, still source='llm'."""
    chain = settings.llm_model_chain
    assert len(chain) >= 2, "default config should provide a fallback model"
    primary, secondary = chain[0], chain[1]

    def fake(messages, model=None):
        if model == primary:
            raise ConnectionError("429 rate-limited upstream")
        return _valid_payload("HIGH", 0.88)

    monkeypatch.setattr(llm_scorer, "_raw_completion", fake)
    result = score_lead(HIGH_VALUE_CONTEXT)

    assert result.source == "llm"                    # an LLM produced it, not the rule engine
    assert result.priority == "HIGH"
    assert result._exchange["model"] == secondary    # the fallback model is recorded


def test_all_models_fail_then_rules(monkeypatch):
    """Every model in the chain failing → rules_fallback, reason names the exhaustion."""
    def down(messages, model=None):
        raise TimeoutError("upstream down")

    monkeypatch.setattr(llm_scorer, "_raw_completion", down)
    result = score_lead(HIGH_VALUE_CONTEXT)

    assert result.source == "rules_fallback"
    assert "all_models_failed" in result._exchange["fallback_reason"]


def test_single_model_chain_disables_failover(monkeypatch):
    """With no fallback models configured, one failure → rules (original behavior)."""
    monkeypatch.setattr(settings, "LLM_FALLBACK_MODELS", "")
    tried = []

    def fake(messages, model=None):
        tried.append(model)
        raise ConnectionError("down")

    monkeypatch.setattr(llm_scorer, "_raw_completion", fake)
    result = score_lead(HIGH_VALUE_CONTEXT)

    assert result.source == "rules_fallback"
    assert set(tried) == {settings.LLM_MODEL}        # only the single primary was attempted
