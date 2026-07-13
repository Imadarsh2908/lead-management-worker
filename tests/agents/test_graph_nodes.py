import uuid
import pytest
from unittest.mock import patch, Mock

from app.agents.state import AgentState, ToolCallRecord, AuditLogEntry
from app.agents.graph import (
    node_receive_lead,
    node_validate,
    node_crm_lookup,
    node_enrichment,
    node_lead_score,
    node_decision,
    node_generate_follow_up,
    node_notify,
    node_retry,
    node_escalate,
    node_audit,
    process_lead,
)
from app.models.lead import Lead, WorkflowState, WorkflowStatus


def test_node_receive_lead():
    state = AgentState(lead_id=uuid.uuid4(), workflow_id=uuid.uuid4())
    res = node_receive_lead(state)
    assert res["status"] == "RECEIVED"
    assert res["current_step"] == "receive_lead"
    assert len(res["audit_logs"]) == 1


def test_node_validate_success():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"email": "test@domain.com", "phone": "+123456"}
    )
    res = node_validate(state)
    assert res["status"] == "VALIDATING"
    assert res["current_step"] == "validate"
    assert len(res["validation_errors"]) == 0


def test_node_validate_failure():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"phone": "+123456"}  # Missing email
    )
    res = node_validate(state)
    assert res["status"] == "VALIDATING"
    assert len(res["validation_errors"]) == 1
    assert "Missing required field" in res["validation_errors"][0]


def test_node_crm_lookup():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"email": "test@domain.com"}
    )
    res = node_crm_lookup(state)
    assert res["current_step"] == "crm_lookup"
    assert res["memory"]["exists_in_crm"] is False


@patch("app.agents.graph.safe_enrich_domain")
def test_node_enrichment_success(mock_safe_enrich):
    mock_safe_enrich.return_value = {
        "domain": "domain.com",
        "company_name": "Domain Corp",
        "industry": "Tech",
        "company_size": "SMB",
        "is_freemail": False,
        "enrichment_failed": False,
    }
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"email": "user@domain.com"}
    )
    res = node_enrichment(state)
    assert res["status"] == "ENRICHING"
    assert res["current_step"] == "enrichment"
    assert res["memory"]["enrichment"]["company_name"] == "Domain Corp"
    assert len(res["tool_history"]) == 1


def test_node_enrichment_no_email():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={}
    )
    res = node_enrichment(state)
    assert res["status"] == "ENRICHING"
    assert res["memory"]["enrichment"]["enrichment_failed"] is True


def test_node_lead_score():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={
            "email": "user@stark.com",
            "budget": 600000.0,
            "job_title": "CEO",
            "enrichment": {"company_size": "Enterprise", "is_freemail": False}
        }
    )
    res = node_lead_score(state)
    assert res["status"] == "ANALYZING"
    assert res["current_step"] == "lead_score"
    assert res["priority"] == "HIGH"
    assert res["confidence"] == 0.88
    assert res["next_action"] == "PROCEED"


def test_node_decision():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        next_action="generate_follow_up"
    )
    res = node_decision(state)
    assert res["current_step"] == "decision"
    assert res["next_action"] == "generate_follow_up"


def test_node_decision_default():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        next_action=None
    )
    res = node_decision(state)
    assert res["next_action"] == "notify"


def test_node_generate_follow_up():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"first_name": "Tony", "company": "Stark Industries"}
    )
    res = node_generate_follow_up(state)
    assert res["current_step"] == "generate_follow_up"
    assert "Tony" in res["memory"]["draft_email"]


def test_node_notify():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        priority="HIGH",
        memory={"email": "tony@stark.com", "company": "Stark Industries", "draft_email": "hi"}
    )
    res = node_notify(state)
    assert res["status"] == "EXECUTING"
    assert res["current_step"] == "notify"
    assert len(res["tool_history"]) == 1


def test_node_retry():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        retry_count=1
    )
    res = node_retry(state)
    assert res["retry_count"] == 2
    assert res["current_step"] == "retry"


def test_node_escalate():
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        validation_errors=["No email"]
    )
    res = node_escalate(state)
    assert res["status"] == "ESCALATED"
    assert res["current_step"] == "escalate"


def test_node_audit(db_session):
    # Setup database records
    lead_id = uuid.uuid4()
    lead = Lead(id=lead_id, email="audit@test.com")
    wf_state = WorkflowState(lead_id=lead_id, current_status=WorkflowStatus.RECEIVED)
    db_session.add_all([lead, wf_state])
    db_session.commit()

    # Call node_audit using testing DB engine via patch
    state = AgentState(
        lead_id=lead_id,
        workflow_id=uuid.uuid4(),
        status="COMPLETED",
        priority="HIGH",
        confidence=0.9,
        memory={"email": "audit@test.com"},
        audit_logs=[
            AuditLogEntry(action_type="STATE_TRANSITION", message="log 1", metadata={}),
            AuditLogEntry(action_type="TOOL_INVOCATION", message="log 2", metadata={}),
        ]
    )

    with patch("app.core.database.engine", db_session.bind):
        res = node_audit(state)
    
    assert res["status"] == "COMPLETED"
    assert res["current_step"] == "audit"

    # Verify DB updates
    db_session.expire_all()
    db_lead = db_session.query(Lead).filter(Lead.id == lead_id).first()
    assert db_lead.priority == "HIGH"
    
    db_wf = db_session.query(WorkflowState).filter(WorkflowState.lead_id == lead_id).first()
    assert db_wf.current_status == WorkflowStatus.COMPLETED


@patch("app.core.logging_config.set_correlation_id")
def test_process_lead(mock_correlate, db_session):
    lead_id = uuid.uuid4()
    lead = Lead(id=lead_id, email="process@test.com", phone="+123")
    wf_state = WorkflowState(lead_id=lead_id, current_status=WorkflowStatus.RECEIVED)
    db_session.add_all([lead, wf_state])
    db_session.commit()

    # process_lead build graph and calls stream.
    # We patch langgraph compile to mock building the graph or run with InMemorySaver checkpointer
    # to avoid needing Redis during test execution.
    from langgraph.checkpoint.memory import MemorySaver
    from app.agents.graph import build_graph

    with patch("app.core.memory.get_checkpointer", return_value=MemorySaver()), patch("app.core.database.engine", db_session.bind):
        process_lead(str(lead_id), str(uuid.uuid4()), {"email": "process@test.com", "phone": "+123"})
