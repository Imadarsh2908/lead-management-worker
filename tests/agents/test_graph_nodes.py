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
from app.models.lead import Lead, WorkflowState, WorkflowStatus, AuditLog, AuditActionType


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


def test_node_crm_lookup_new_lead(db_session):
    """A lead whose email matches no OTHER lead is treated as new (exists_in_crm=False)."""
    lead_id = uuid.uuid4()
    db_session.add(Lead(id=lead_id, email="onlyme@domain.com"))
    db_session.commit()

    state = AgentState(
        lead_id=lead_id,
        workflow_id=uuid.uuid4(),
        memory={"email": "onlyme@domain.com"},
    )
    with patch("app.core.database.engine", db_session.bind):
        res = node_crm_lookup(state)

    assert res["current_step"] == "crm_lookup"
    # Only match is the lead itself, which is excluded → not a duplicate.
    assert res["memory"]["exists_in_crm"] is False


def test_node_crm_lookup_existing_lead(db_session):
    """If another lead already has this email, exists_in_crm must be True."""
    existing_id = uuid.uuid4()
    current_id = uuid.uuid4()
    db_session.add(Lead(id=existing_id, email="dup@domain.com"))
    db_session.commit()

    state = AgentState(
        lead_id=current_id,
        workflow_id=uuid.uuid4(),
        memory={"email": "dup@domain.com"},
    )
    with patch("app.core.database.engine", db_session.bind):
        res = node_crm_lookup(state)

    assert res["memory"]["exists_in_crm"] is True


def test_node_crm_lookup_db_error_degrades():
    """On a DB error the node degrades gracefully: exists_in_crm=False + audit warning."""
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"email": "boom@domain.com"},
    )
    # Force the Session context manager to raise. node_crm_lookup imports Session
    # lazily from sqlalchemy.orm, so we patch it at the source.
    with patch("sqlalchemy.orm.Session", side_effect=RuntimeError("DB down")):
        res = node_crm_lookup(state)

    assert res["memory"]["exists_in_crm"] is False
    assert any("degraded" in log.message.lower() for log in res["audit_logs"])


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
    # Failure bumps the dedicated enrichment counter (not the general retry_count).
    assert res["enrichment_retry_count"] == 1


@patch("app.agents.graph.safe_enrich_domain")
def test_node_enrichment_failure_increments_dedicated_counter(mock_safe_enrich):
    """A failed enrichment increments enrichment_retry_count and leaves retry_count alone."""
    mock_safe_enrich.return_value = {"company_name": None, "enrichment_failed": True}
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        retry_count=1,
        enrichment_retry_count=1,
        memory={"email": "user@domain.com"},
    )
    res = node_enrichment(state)
    assert res["enrichment_retry_count"] == 2      # bumped
    assert "retry_count" not in res                # general breaker untouched


@patch("app.agents.graph.safe_enrich_domain")
def test_node_enrichment_success_does_not_increment_counter(mock_safe_enrich):
    """A successful enrichment must not increment enrichment_retry_count."""
    mock_safe_enrich.return_value = {"company_name": "Acme", "enrichment_failed": False}
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        enrichment_retry_count=2,
        memory={"email": "user@domain.com"},
    )
    res = node_enrichment(state)
    assert res["enrichment_retry_count"] == 2      # unchanged


def test_node_lead_score():
    """LLM HIGH verdict on a genuinely high-value lead propagates unchanged."""
    from app.agents.llm_scorer import ScoringResult

    llm_result = ScoringResult(
        priority="HIGH", confidence=0.9, next_action="generate_follow_up",
        reasoning=["High budget", "CEO decision maker"], source="llm",
    )
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
    with patch("app.agents.graph.score_lead", return_value=llm_result):
        res = node_lead_score(state)

    assert res["status"] == "ANALYZING"
    assert res["current_step"] == "lead_score"
    assert res["priority"] == "HIGH"
    assert res["confidence"] == 0.9           # LLM confidence propagates (no more hardcoded 0.88)
    assert res["next_action"] == "generate_follow_up"
    # The full LLM exchange is captured as an auditable tool call.
    tool_names = [t.tool_name for t in res["tool_history"]]
    assert "llm_score_lead" in tool_names


def test_node_lead_score_guardrail_downgrades_freemail():
    """LLM says HIGH for a freemail lead → rule guardrail downgrades to LOW with an override audit."""
    from app.agents.llm_scorer import ScoringResult

    llm_result = ScoringResult(
        priority="HIGH", confidence=0.95, next_action="generate_follow_up",
        reasoning=["Model over-scored this one"], source="llm",
    )
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={
            "email": "user@gmail.com",
            "budget": 0.0,
            "job_title": "",
            "enrichment": {"is_freemail": True},
        },
    )
    with patch("app.agents.graph.score_lead", return_value=llm_result):
        res = node_lead_score(state)

    # Guardrail may only make it stricter: HIGH → LOW, action follow_up → notify.
    assert res["priority"] == "LOW"
    assert res["next_action"] == "notify"
    override_entries = [l for l in res["audit_logs"] if l.action_type == "GUARDRAIL_OVERRIDE"]
    assert len(override_entries) >= 1
    assert override_entries[0].metadata["before"] == "HIGH"
    assert override_entries[0].metadata["after"] == "LOW"


def test_node_lead_score_fallback_escalates():
    """A rules_fallback (confidence 0.50) forces escalation via the guardrail + gate."""
    from app.agents.llm_scorer import ScoringResult
    from app.agents.graph import route_after_decision

    fallback = ScoringResult(
        priority="MEDIUM", confidence=0.50, next_action="notify",
        reasoning=["Rule-based fallback (llm down)."], source="rules_fallback",
    )
    state = AgentState(
        lead_id=uuid.uuid4(),
        workflow_id=uuid.uuid4(),
        memory={"email": "user@corp.com", "budget": 50000.0, "job_title": "Manager"},
    )
    with patch("app.agents.graph.score_lead", return_value=fallback):
        res = node_lead_score(state)

    assert res["confidence"] == 0.50
    # Low confidence trips the rule engine's LowConfidenceRule → forced escalate.
    assert res["next_action"] == "ESCALATE"
    # And the downstream router escalates on the 0.50 confidence gate too.
    routed = route_after_decision(AgentState(
        lead_id=uuid.uuid4(), workflow_id=uuid.uuid4(),
        confidence=res["confidence"], next_action=res["next_action"],
    ))
    assert routed == "escalate"


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


def test_node_audit_persists_tool_io(db_session):
    """node_audit must write one AuditLog row per ToolCallRecord with tool I/O populated."""
    lead_id = uuid.uuid4()
    db_session.add_all([
        Lead(id=lead_id, email="tools@test.com"),
        WorkflowState(lead_id=lead_id, current_status=WorkflowStatus.RECEIVED),
    ])
    db_session.commit()

    state = AgentState(
        lead_id=lead_id,
        workflow_id=uuid.uuid4(),
        status="COMPLETED",
        priority="MEDIUM",
        confidence=0.9,
        tool_history=[
            ToolCallRecord(
                tool_name="enrich_lead_domain",
                inputs={"email": "tools@test.com"},
                outputs={"company_size": "SMB", "enrichment_failed": False},
                success=True,
            ),
            ToolCallRecord(
                tool_name="send_slack_notification",
                inputs={"lead_id": str(lead_id)},
                outputs={"status": "sent"},
                success=True,
            ),
        ],
        audit_logs=[
            AuditLogEntry(action_type="STATE_TRANSITION", message="reasoning row", metadata={}),
        ],
    )

    with patch("app.core.database.engine", db_session.bind):
        node_audit(state)

    db_session.expire_all()
    tool_rows = (
        db_session.query(AuditLog)
        .filter(AuditLog.lead_id == lead_id)
        .filter(AuditLog.action_type == AuditActionType.TOOL_INVOCATION)
        .all()
    )
    # One row per ToolCallRecord.
    assert len(tool_rows) == 2

    by_input = {row.tool_inputs.get("email") or row.tool_inputs.get("lead_id"): row for row in tool_rows}
    enrich_row = by_input["tools@test.com"]
    assert enrich_row.tool_inputs == {"email": "tools@test.com"}
    assert enrich_row.tool_outputs["company_size"] == "SMB"
    assert enrich_row.llm_reasoning["tool_name"] == "enrich_lead_domain"

    slack_row = by_input[str(lead_id)]
    assert slack_row.tool_outputs == {"status": "sent"}


@patch("app.core.logging_config.set_correlation_id")
def test_process_lead(mock_correlate, db_session):
    lead_id = uuid.uuid4()
    lead = Lead(id=lead_id, email="process@test.com", phone="+123")
    wf_state = WorkflowState(lead_id=lead_id, current_status=WorkflowStatus.RECEIVED)
    db_session.add_all([lead, wf_state])
    db_session.commit()

    # process_lead builds (once) the compiled graph and streams it.
    # We run with an in-memory checkpointer to avoid needing Redis, patch the DB
    # engine to the test SQLite DB, and stub enrichment to SUCCEED so the workflow
    # takes the happy path instead of the (now-wired) retry loop hitting real HTTP.
    from langgraph.checkpoint.memory import MemorySaver
    from app.agents.llm_scorer import ScoringResult
    import app.agents.graph as graph_module

    successful_enrichment = {
        "company_name": "Process Corp",
        "industry": "Tech",
        "company_size": "SMB",
        "is_freemail": False,
        "enrichment_failed": False,
    }
    # Stub the LLM so the scoring node stays hermetic (no network).
    llm_result = ScoringResult(
        priority="MEDIUM", confidence=0.82, next_action="notify",
        reasoning=["stubbed"], source="llm",
    )

    # Reset the compiled-graph singleton so this test builds a fresh graph that
    # picks up the patched checkpointer rather than reusing one from another test.
    graph_module._compiled_graph = None

    with patch("app.core.memory.get_checkpointer", return_value=MemorySaver()), \
         patch("app.agents.graph.get_checkpointer", return_value=MemorySaver()), \
         patch("app.agents.graph.safe_enrich_domain", return_value=successful_enrichment), \
         patch("app.agents.graph.score_lead", return_value=llm_result), \
         patch("app.core.database.engine", db_session.bind):
        process_lead(str(lead_id), str(uuid.uuid4()), {"email": "process@test.com", "phone": "+123"})

    # Clean up the singleton so a graph bound to test patches doesn't leak.
    graph_module._compiled_graph = None
