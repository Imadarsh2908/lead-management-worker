"""
tests/test_policy.py
--------------------
Proves that behavior is driven by the externalized instruction/policy files:
  - editing config/policy.yaml (confidence_gate) changes routing with no code change
  - editing AGENTS.md is hot-reloaded by llm_scorer with no process restart
  - an invalid policy file fails fast at load time
"""
import os
import uuid

import pytest

from app.core.policy import reload_policy


# A complete, valid policy that only differs from the default in confidence_gate.
_POLICY_GATE_095 = """
decision:
  high_budget_threshold: 500000
  confidence_gate: 0.95
  decision_maker_titles: [ceo, founder, vp, director, cmo, cto, coo, president]
  freemail_domains: [gmail.com, yahoo.com, hotmail.com, outlook.com, live.com, icloud.com, protonmail.com, aol.com]
workflow:
  max_retries: 3
resilience:
  crm_breaker:        {fail_max: 5, reset_timeout: 60}
  enrichment_breaker: {fail_max: 3, reset_timeout: 30}
  llm_breaker:        {fail_max: 3, reset_timeout: 60}
  enrichment_timeout_seconds: 5
  crm_timeout_seconds: 3
"""


@pytest.fixture
def restore_policy():
    """Ensure the cached policy is rebuilt from the real file after each test."""
    yield
    # Clear any override explicitly (don't depend on fixture teardown ordering)
    # and rebuild the default policy so later tests see the real values.
    os.environ.pop("POLICY_PATH", None)
    reload_policy()


def _decision_state(confidence):
    from app.agents.state import AgentState
    return AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=confidence,
        next_action="generate_follow_up",
    )


def test_policy_override_changes_confidence_gate(tmp_path, monkeypatch, restore_policy):
    """A 0.88-confidence lead proceeds under the default gate (0.70) but escalates
    when policy.yaml raises the gate to 0.95 — behavior changed by file edit alone."""
    from app.agents.graph import route_after_decision

    # Baseline: default policy (gate 0.70) → 0.88 confidence proceeds.
    reload_policy()
    assert route_after_decision(_decision_state(0.88)) == "generate_follow_up"

    # Edit "the file": point at a policy with confidence_gate = 0.95.
    custom = tmp_path / "policy.yaml"
    custom.write_text(_POLICY_GATE_095, encoding="utf-8")
    monkeypatch.setenv("POLICY_PATH", str(custom))
    reload_policy()

    # Same 0.88 lead now escalates (0.88 < 0.95) — no code changed.
    assert route_after_decision(_decision_state(0.88)) == "escalate"


def test_invalid_policy_fails_fast(tmp_path, monkeypatch, restore_policy):
    """A malformed / incomplete policy raises a clear RuntimeError at load time."""
    bad = tmp_path / "policy.yaml"
    # confidence_gate out of range and required sections missing.
    bad.write_text("decision:\n  confidence_gate: 5.0\n", encoding="utf-8")
    monkeypatch.setenv("POLICY_PATH", str(bad))
    with pytest.raises(RuntimeError):
        reload_policy()


def test_agents_prompt_hot_reload(tmp_path, monkeypatch):
    """Editing the prompt file is picked up by llm_scorer without a restart (mtime cache)."""
    from app.core.config import settings
    from app.agents import llm_scorer

    prompt = tmp_path / "AGENTS.md"
    prompt.write_text("SYSTEM PROMPT VERSION ONE", encoding="utf-8")
    monkeypatch.setattr(settings, "AGENT_PROMPT_PATH", str(prompt))

    assert "VERSION ONE" in llm_scorer.get_system_prompt()

    # Edit the file and force a distinct mtime so the cache key changes regardless
    # of filesystem timestamp resolution.
    prompt.write_text("SYSTEM PROMPT VERSION TWO", encoding="utf-8")
    st = os.stat(prompt)
    os.utime(prompt, (st.st_atime, st.st_mtime + 10))

    reloaded = llm_scorer.get_system_prompt()
    assert "VERSION TWO" in reloaded
    assert "VERSION ONE" not in reloaded
