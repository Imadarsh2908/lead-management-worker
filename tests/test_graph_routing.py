"""
tests/test_graph_routing.py
----------------------------
Unit tests for the LangGraph routing edges.
Tests state transitions and AI confidence guardrails.
"""
import uuid
import pytest
from app.agents.graph import (
    route_after_validate,
    route_after_crm,
    route_after_decision,
    route_after_enrichment,
    route_after_retry,
)
from app.agents.state import AgentState


def test_routing_validation_success():
    """If there are no validation errors, proceed to CRM lookup."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        validation_errors=[],
    )
    assert route_after_validate(state) == "crm_lookup"


def test_routing_validation_failure():
    """If validation has errors, escalate immediately."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        validation_errors=["Email is missing"],
    )
    assert route_after_validate(state) == "escalate"


def test_routing_crm_new_lead():
    """If the lead does not exist in CRM, proceed to company domain enrichment."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"exists_in_crm": False},
    )
    assert route_after_crm(state) == "enrichment"


def test_routing_crm_existing_lead():
    """If lead is already in CRM, skip enrichment and jump to lead scoring."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"exists_in_crm": True},
    )
    assert route_after_crm(state) == "lead_score"


def test_routing_decision_low_confidence():
    """Confidence below 70% must escalate, overriding engine recommendation."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=0.69,
        next_action="generate_follow_up",
    )
    assert route_after_decision(state) == "escalate"


def test_routing_decision_proceed_follow_up():
    """High priority lead with high confidence must route to follow-up draft."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=0.85,
        next_action="generate_follow_up",
    )
    assert route_after_decision(state) == "generate_follow_up"


def test_routing_decision_proceed_notify():
    """Low/medium priority lead with high confidence routes directly to notify."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=0.90,
        next_action="notify",
    )
    assert route_after_decision(state) == "notify"


def test_routing_retry_under_limit():
    """Retry count < 3 should retry by starting from validation again."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        retry_count=2,
    )
    assert route_after_retry(state) == "validate"


def test_routing_retry_over_limit():
    """Retry count >= 3 must escalate to human rather than loop endlessly."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        retry_count=3,
    )
    assert route_after_retry(state) == "escalate"


# ─────────────────────────────────────────────────────────────
# Bug 1 — route_after_decision full action matrix
# ─────────────────────────────────────────────────────────────

def _decision_state(action):
    return AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=0.90,          # Above the guardrail so action decides the route
        next_action=action,
    )


@pytest.mark.parametrize(
    "action,expected",
    [
        ("generate_follow_up", "generate_follow_up"),  # HIGH / Senior Sales
        ("notify", "notify"),                          # MEDIUM
        ("notify", "notify"),                          # LOW (same action as MEDIUM)
        ("ASK_USER", "escalate"),                      # cannot proceed autonomously
        ("ESCALATE", "escalate"),                      # explicit escalation
        ("some_unexpected_action", "escalate"),        # unknown → fail safe
        (None, "escalate"),                            # missing action → fail safe
    ],
)
def test_route_after_decision_matrix(action, expected):
    assert route_after_decision(_decision_state(action)) == expected


def test_route_after_decision_confidence_overrides_action():
    """Low confidence escalates even when the action would otherwise proceed."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        confidence=0.50,
        next_action="generate_follow_up",
    )
    assert route_after_decision(state) == "escalate"


# ─────────────────────────────────────────────────────────────
# Bug 2 — enrichment retry loop & graceful degradation
# ─────────────────────────────────────────────────────────────

def _enrichment_state(*, failed, enrichment_retry_count):
    return AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        enrichment_retry_count=enrichment_retry_count,
        memory={"enrichment": {"enrichment_failed": failed}},
    )


def test_route_after_enrichment_success_proceeds():
    """Successful enrichment proceeds straight to scoring."""
    assert route_after_enrichment(_enrichment_state(failed=False, enrichment_retry_count=0)) == "lead_score"


@pytest.mark.parametrize("enrichment_retry_count", [1, 2])
def test_route_after_enrichment_retries_while_attempts_remain(enrichment_retry_count):
    """Enrichment failure with attempts left routes into the retry loop."""
    assert route_after_enrichment(
        _enrichment_state(failed=True, enrichment_retry_count=enrichment_retry_count)
    ) == "retry"


def test_route_after_enrichment_final_attempt_degrades_not_escalates():
    """
    Bug 2 rule: after enrichment has failed 3 times (enrichment_retry_count == 3)
    the workflow must PROCEED to lead_score with degraded data — it must NOT
    escalate purely for missing enrichment.
    """
    state = _enrichment_state(failed=True, enrichment_retry_count=3)
    assert route_after_enrichment(state) == "lead_score"
    # The failure flag is preserved so the scorer knows the data is degraded.
    assert state.memory["enrichment"]["enrichment_failed"] is True


def test_enrichment_failure_does_not_trip_general_circuit_breaker():
    """
    Separation of concerns: the general retry_count breaker escalates at >= 3,
    but enrichment failures ride enrichment_retry_count. A lead that has failed
    enrichment 3 times (retry_count still low) proceeds — it does NOT escalate.
    """
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        retry_count=2,                 # general breaker still under limit
        enrichment_retry_count=3,      # enrichment attempts exhausted
        memory={"enrichment": {"enrichment_failed": True}},
    )
    assert route_after_enrichment(state) == "lead_score"
    # The general breaker, checked independently, would still escalate at >= 3.
    assert route_after_retry(
        AgentState(lead_id=uuid.uuid4(), workflow_id=uuid.uuid4(), retry_count=3)
    ) == "escalate"


def test_validation_failure_still_escalates():
    """Validation failures (unlike enrichment failures) DO escalate."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        validation_errors=["Missing required field: email"],
    )
    assert route_after_validate(state) == "escalate"
