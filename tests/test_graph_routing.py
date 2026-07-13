"""
tests/test_graph_routing.py
----------------------------
Unit tests for the LangGraph routing edges.
Tests state transitions and AI confidence guardrails.
"""
import uuid
from app.agents.graph import (
    route_after_validate,
    route_after_crm,
    route_after_decision,
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
