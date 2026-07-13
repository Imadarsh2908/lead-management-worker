"""
app/agents/graph.py
--------------------
The core LangGraph StateGraph — the brain of the Autonomous Lead Management Worker.

How it works:
  - Each function is a "node" in the graph that receives the current AgentState,
    performs a specific action, and returns a dict of state updates.
  - LangGraph merges those updates back into the state automatically.
  - Conditional edge functions inspect the state to decide which node to run next.
  - Redis checkpointing (via get_checkpointer()) saves state after every node,
    enabling crash recovery without reprocessing from the beginning.

Nodes in this graph:
  receive_lead → validate → crm_lookup → enrichment → lead_score → decision
             ↘ escalate ↗            ↘ lead_score ↗         ↘ generate_follow_up → notify → audit → END
                                                              ↘ escalate → audit → END
"""
import uuid
from datetime import datetime, timezone
from typing import Literal

from loguru import logger

from app.agents.state import AgentState, AuditLogEntry, ToolCallRecord
from app.agents.decision_engine import DecisionEngine, LeadContext
from app.core.memory import get_checkpointer
from app.core.resilience import safe_enrich_domain, safe_update_crm, validate_required_fields

try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logger.warning("LangGraph not installed. Graph compilation will be skipped.")


# ─────────────────────────────────────────────────────────────
# HELPER: Append a structured audit log entry to state
# ─────────────────────────────────────────────────────────────

def _audit(state: AgentState, action_type: str, message: str, **metadata) -> AuditLogEntry:
    """Creates an audit log entry with timestamp and context metadata."""
    return AuditLogEntry(
        action_type=action_type,
        message=message,
        metadata={
            "workflow_id": str(state.workflow_id),
            "lead_id": str(state.lead_id),
            "current_step": state.current_step,
            **metadata,
        },
    )


# ─────────────────────────────────────────────────────────────
# NODE IMPLEMENTATIONS
# Each node receives the full AgentState and returns a dict of updates.
# ─────────────────────────────────────────────────────────────

def node_receive_lead(state: AgentState) -> dict:
    """
    NODE: receive_lead
    Entry point. Logs the initial ingestion event.
    Transitions status from unset → RECEIVED.
    """
    logger.info(f"[GRAPH] Receiving lead {state.lead_id} for workflow {state.workflow_id}")
    return {
        "status": "RECEIVED",
        "current_step": "receive_lead",
        "audit_logs": state.audit_logs + [
            _audit(state, "STATE_TRANSITION", "Lead ingested and workflow started.")
        ],
    }


def node_validate(state: AgentState) -> dict:
    """
    NODE: validate
    Checks that the lead payload contains the minimum required fields.
    If validation fails, sets validation_errors for the routing edge to act on.
    """
    logger.info(f"[GRAPH] Validating lead {state.lead_id}")
    payload = state.memory  # The raw lead payload lives in memory
    validation_result = validate_required_fields(payload)
    errors = []

    if not validation_result["valid"]:
        errors = [f"Missing required field: {f}" for f in validation_result["missing"]]

    return {
        "status": "VALIDATING",
        "current_step": "validate",
        "validation_errors": errors,
        "audit_logs": state.audit_logs + [
            _audit(
                state, "STATE_TRANSITION",
                f"Validation complete. Errors: {errors if errors else 'None'}",
                validation_errors=errors,
            )
        ],
    }


def node_crm_lookup(state: AgentState) -> dict:
    """
    NODE: crm_lookup
    Checks if this lead already exists in the CRM to prevent duplicate processing.
    Uses a simulated lookup here — replace with actual CRM API call in production.
    """
    logger.info(f"[GRAPH] CRM lookup for lead {state.lead_id}")
    email = state.memory.get("email", "")

    # Simulated CRM lookup — in production, this would call safe_update_crm()
    # with a GET request or query your CRM SDK.
    exists_in_crm = False  # Default: assume new lead for demo purposes

    return {
        "current_step": "crm_lookup",
        "memory": {**state.memory, "exists_in_crm": exists_in_crm},
        "audit_logs": state.audit_logs + [
            _audit(state, "TOOL_INVOCATION", f"CRM lookup complete. Exists: {exists_in_crm}")
        ],
    }


def node_enrichment(state: AgentState) -> dict:
    """
    NODE: enrichment
    Calls the domain enrichment API to fetch company context.
    Uses graceful degradation — if the API is down, the workflow continues
    with null company fields (enrichment_failed=True in memory).
    """
    logger.info(f"[GRAPH] Enriching lead {state.lead_id}")
    email = state.memory.get("email", "")

    if email:
        domain = email.split("@")[-1]
        enrichment_data = safe_enrich_domain(domain)
    else:
        enrichment_data = {"enrichment_failed": True}

    # Record the tool call in tool_history so the LLM knows what was called
    tool_record = ToolCallRecord(
        tool_name="enrich_lead_domain",
        inputs={"email": email},
        outputs=enrichment_data,
        success=not enrichment_data.get("enrichment_failed", False),
    )

    return {
        "status": "ENRICHING",
        "current_step": "enrichment",
        "memory": {**state.memory, "enrichment": enrichment_data},
        "tool_history": state.tool_history + [tool_record],
        "audit_logs": state.audit_logs + [
            _audit(
                state, "TOOL_INVOCATION",
                f"Domain enrichment completed. Failed: {enrichment_data.get('enrichment_failed')}",
                enrichment=enrichment_data,
            )
        ],
    }


def node_lead_score(state: AgentState) -> dict:
    """
    NODE: lead_score
    Evaluates the enriched lead context using the Decision Engine.
    Sets priority and confidence on the state.
    
    In a real deployment, this node would call the LLM (e.g., OpenAI GPT-3.5)
    to produce a confidence score based on the system prompt and lead context.
    For this demo, we use the rule-based Decision Engine to simulate LLM output.
    """
    logger.info(f"[GRAPH] Scoring lead {state.lead_id}")
    enrichment = state.memory.get("enrichment", {})

    # Build context for the Decision Engine from accumulated state
    context = LeadContext(
        email=state.memory.get("email"),
        budget=state.memory.get("budget", 0.0),
        job_title=state.memory.get("job_title", ""),
        ai_confidence=0.88,  # In production: parsed from LLM response JSON
        company_size=enrichment.get("company_size"),
        is_freemail=enrichment.get("is_freemail", False),
    )

    engine = DecisionEngine()
    decision = engine.process_lead(context)

    return {
        "status": "ANALYZING",
        "current_step": "lead_score",
        "priority": decision.priority,
        "confidence": context.ai_confidence,
        "next_action": decision.action,
        "memory": {**state.memory, "decision": decision.model_dump()},
        "audit_logs": state.audit_logs + [
            _audit(
                state, "LLM_REASONING",
                f"Lead scored: priority={decision.priority}, action={decision.action}",
                reasoning=decision.reasoning,
                confidence=context.ai_confidence,
            )
        ],
    }


def node_decision(state: AgentState) -> dict:
    """
    NODE: decision
    Reads the scoring output and finalizes what should happen next.
    The routing edge (route_after_decision) reads state.next_action to route.
    """
    logger.info(f"[GRAPH] Making final decision for lead {state.lead_id}")

    # If no action was explicitly set by scoring (shouldn't happen), default to notify
    if not state.next_action:
        next_action = "notify"
    else:
        next_action = state.next_action

    return {
        "current_step": "decision",
        "next_action": next_action,
    }


def node_generate_follow_up(state: AgentState) -> dict:
    """
    NODE: generate_follow_up
    Drafts a personalized follow-up message for high-priority leads.
    In production: this calls the LLM with lead context to generate a custom email.
    """
    logger.info(f"[GRAPH] Generating follow-up for lead {state.lead_id}")
    decision = state.memory.get("decision", {})

    # Simulated LLM-generated draft — replace with actual LLM call in production
    draft_email = (
        f"Hi {state.memory.get('first_name', 'there')},\n\n"
        f"I noticed your interest and wanted to follow up personally. "
        f"Given your role at {state.memory.get('company', 'your company')}, "
        f"I'd love to discuss how we can help. Are you available for a quick call?\n\n"
        f"Best regards,\nThe Team"
    )

    return {
        "current_step": "generate_follow_up",
        "memory": {**state.memory, "draft_email": draft_email},
        "audit_logs": state.audit_logs + [
            _audit(state, "LLM_REASONING", "Follow-up email draft generated.")
        ],
    }


def node_notify(state: AgentState) -> dict:
    """
    NODE: notify
    Sends the lead summary and (if generated) the follow-up draft to
    the sales team via Slack/email notification.
    In production: call a Slack webhook or send an email via SendGrid.
    """
    logger.info(f"[GRAPH] Sending notification for lead {state.lead_id}")

    # Simulated notification — replace with requests.post(SLACK_WEBHOOK_URL, ...) in production
    notification_payload = {
        "lead_id": str(state.lead_id),
        "priority": state.priority,
        "email": state.memory.get("email"),
        "company": state.memory.get("company"),
        "draft_email": state.memory.get("draft_email"),
    }

    return {
        "status": "EXECUTING",
        "current_step": "notify",
        "tool_history": state.tool_history + [
            ToolCallRecord(
                tool_name="send_slack_notification",
                inputs=notification_payload,
                outputs={"status": "sent"},
                success=True,
            )
        ],
        "audit_logs": state.audit_logs + [
            _audit(state, "TOOL_INVOCATION", "Sales team notified via Slack/Email.")
        ],
    }


def node_retry(state: AgentState) -> dict:
    """
    NODE: retry
    Increments the retry counter.
    The routing edge checks if retry_count >= 3 and routes to escalation if so.
    """
    new_count = state.retry_count + 1
    logger.warning(f"[GRAPH] Retry {new_count} for lead {state.lead_id}")
    return {
        "retry_count": new_count,
        "current_step": "retry",
        "audit_logs": state.audit_logs + [
            _audit(state, "STATE_TRANSITION", f"Retry attempt {new_count} triggered.")
        ],
    }


def node_escalate(state: AgentState) -> dict:
    """
    NODE: escalate
    Human-in-the-loop handoff. Called when:
      - Validation fails (missing required fields)
      - AI confidence is too low
      - Max retries are exhausted

    In production: this would send a high-priority Slack DM to the manager,
    create a ticket in Jira/Zendesk, and pause the workflow.
    """
    logger.warning(f"[GRAPH] Escalating lead {state.lead_id} to human. Reason: {state.validation_errors}")

    return {
        "status": "ESCALATED",
        "current_step": "escalate",
        "audit_logs": state.audit_logs + [
            _audit(
                state, "ESCALATION",
                "Lead escalated to human agent for manual review.",
                validation_errors=state.validation_errors,
                confidence=state.confidence,
                retry_count=state.retry_count,
            )
        ],
    }


def node_audit(state: AgentState) -> dict:
    """
    NODE: audit (FINAL NODE — runs for EVERY workflow path)

    Persists the complete workflow history to PostgreSQL.
    This is the convergence point for ALL execution paths:
      - Successful completion → reaches audit → writes COMPLETED status
      - Escalation → reaches audit → writes ESCALATED status

    Design: We do the DB write HERE (not in individual nodes) to:
      1. Minimize DB round-trips (one bulk insert vs. many small inserts)
      2. Guarantee atomicity (all logs saved together or none are)
      3. Keep individual nodes pure and fast
    """
    from sqlalchemy.orm import Session
    from app.core.database import engine
    from app.models.lead import AuditLog, Lead, WorkflowState, WorkflowStatus as DBWorkflowStatus

    logger.info(f"[GRAPH] Audit node: persisting workflow {state.workflow_id} to database.")

    # Determine final status
    final_status = "COMPLETED" if state.status != "ESCALATED" else "ESCALATED"

    try:
        with Session(engine) as db:
            # 1. Update Lead priority in DB
            lead = db.query(Lead).filter(Lead.id == state.lead_id).first()
            if lead:
                lead.priority = state.priority

            # 2. Update WorkflowState status in DB
            wf_state = db.query(WorkflowState).filter(
                WorkflowState.lead_id == state.lead_id
            ).first()
            if wf_state:
                wf_state.current_status = DBWorkflowStatus(final_status)

            # 3. Bulk-insert all audit log entries (single transaction)
            for log_entry in state.audit_logs:
                db_log = AuditLog(
                    lead_id=state.lead_id,
                    action_type=log_entry.action_type,
                    tool_inputs=log_entry.metadata.get("inputs"),
                    tool_outputs=log_entry.metadata.get("outputs"),
                    llm_reasoning={
                        "message": log_entry.message,
                        "confidence": state.confidence,
                        "workflow_id": str(state.workflow_id),
                    },
                )
                db.add(db_log)

            db.commit()
            logger.info(
                f"[GRAPH] Workflow {state.workflow_id} completed. "
                f"Status: {final_status}, Priority: {state.priority}, "
                f"Logs: {len(state.audit_logs)}"
            )
    except Exception as e:
        logger.error(f"[GRAPH] Audit node DB write failed for workflow {state.workflow_id}: {e}")

    return {
        "status": final_status,
        "current_step": "audit",
        "audit_logs": state.audit_logs + [
            _audit(state, "STATE_TRANSITION", f"Workflow finalized with status: {final_status}")
        ],
    }



# ─────────────────────────────────────────────────────────────
# CONDITIONAL ROUTING EDGES
# These functions inspect the state and return the name of the next node.
# Literal type hints document the valid routing options.
# ─────────────────────────────────────────────────────────────

def route_after_validate(state: AgentState) -> Literal["crm_lookup", "escalate"]:
    """
    If validation found errors (e.g., missing email), escalate immediately.
    There's no point enriching or scoring a lead we can't even contact.
    """
    if state.validation_errors:
        logger.info("[ROUTE] Validation failed → escalating")
        return "escalate"
    return "crm_lookup"


def route_after_crm(state: AgentState) -> Literal["enrichment", "lead_score"]:
    """
    If lead already exists in CRM (returning customer), skip enrichment
    since we likely already have their company data. Jump straight to scoring.
    """
    if state.memory.get("exists_in_crm"):
        logger.info("[ROUTE] Lead found in CRM → skipping enrichment")
        return "lead_score"
    return "enrichment"


def route_after_decision(state: AgentState) -> Literal["generate_follow_up", "notify", "escalate"]:
    """
    This is the most critical routing edge — the AI confidence guardrail.
    
    Even if the Decision Engine decided to "generate_follow_up", if the
    underlying confidence score is too low, we OVERRIDE that decision
    and escalate to a human. The agent's recommendation is advisory,
    not absolute, when confidence is below the threshold.
    """
    # CONFIDENCE GUARDRAIL: override any action if AI isn't sure enough
    if state.confidence < 0.70:
        logger.warning(f"[ROUTE] Confidence {state.confidence:.0%} < 70% → escalating")
        return "escalate"

    # Route based on the Decision Engine's recommendation
    if state.next_action == "generate_follow_up" or state.next_action == "PROCEED":
        return "generate_follow_up"
    elif state.next_action == "ESCALATE":
        return "escalate"
    else:
        # Default for LOW/MEDIUM priority leads: just notify without drafting email
        return "notify"


def route_after_retry(state: AgentState) -> Literal["escalate", "validate"]:
    """
    If we've retried 3 or more times, we're in an infinite failure loop.
    Break the loop by escalating to a human rather than retrying forever.
    """
    if state.retry_count >= 3:
        logger.error(f"[ROUTE] Max retries ({state.retry_count}) reached → escalating")
        return "escalate"
    # Restart from validation on retry (re-check the data in case it changed)
    return "validate"


# ─────────────────────────────────────────────────────────────
# GRAPH COMPILATION
# ─────────────────────────────────────────────────────────────

def build_graph():
    """
    Constructs, configures, and compiles the LangGraph StateGraph.
    
    Returns the compiled app ready for .invoke() or .stream() calls.
    
    Usage:
        graph_app = build_graph()
        config = {"configurable": {"thread_id": str(workflow_id)}}
        result = graph_app.invoke(initial_state, config=config)
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError("LangGraph is not installed. Run: pip install langgraph")

    # Initialize the graph with our Pydantic state schema
    workflow = StateGraph(AgentState)

    # ── Register Nodes ────────────────────────────────────
    workflow.add_node("receive_lead", node_receive_lead)
    workflow.add_node("validate", node_validate)
    workflow.add_node("crm_lookup", node_crm_lookup)
    workflow.add_node("enrichment", node_enrichment)
    workflow.add_node("lead_score", node_lead_score)
    workflow.add_node("decision", node_decision)
    workflow.add_node("generate_follow_up", node_generate_follow_up)
    workflow.add_node("notify", node_notify)
    workflow.add_node("retry", node_retry)
    workflow.add_node("escalate", node_escalate)
    workflow.add_node("audit", node_audit)

    # ── Define Entry Point ────────────────────────────────
    workflow.set_entry_point("receive_lead")

    # ── Define Edges (Fixed) ──────────────────────────────
    # Linear edges: no branching, always go to the next node
    workflow.add_edge("receive_lead", "validate")
    workflow.add_edge("enrichment", "lead_score")
    workflow.add_edge("lead_score", "decision")
    workflow.add_edge("generate_follow_up", "notify")
    workflow.add_edge("notify", "audit")
    workflow.add_edge("escalate", "audit")  # Escalation also goes to audit (for logging)
    workflow.add_edge("audit", END)          # Workflow terminates after audit

    # ── Define Conditional Edges ──────────────────────────
    # Branching edges: routing function decides the next node at runtime
    workflow.add_conditional_edges(
        "validate",
        route_after_validate,
        {
            "crm_lookup": "crm_lookup",
            "escalate": "escalate",
        }
    )
    workflow.add_conditional_edges(
        "crm_lookup",
        route_after_crm,
        {
            "enrichment": "enrichment",
            "lead_score": "lead_score",
        }
    )
    workflow.add_conditional_edges(
        "decision",
        route_after_decision,
        {
            "generate_follow_up": "generate_follow_up",
            "notify": "notify",
            "escalate": "escalate",
        }
    )
    workflow.add_conditional_edges(
        "retry",
        route_after_retry,
        {
            "validate": "validate",
            "escalate": "escalate",
        }
    )

    # ── Attach Redis Checkpointer ─────────────────────────
    # This is what enables state recovery on crashes.
    checkpointer = get_checkpointer()

    # Compile into a runnable app
    compiled_app = workflow.compile(checkpointer=checkpointer)
    logger.info("[GRAPH] LangGraph workflow compiled successfully.")
    return compiled_app


def process_lead(lead_id: str, workflow_id: str, lead_payload: dict):
    """
    Entry point to process a single lead through the full LangGraph workflow.
    
    The workflow_id is used as the thread_id for Redis checkpointing.
    If this exact workflow_id is re-submitted (e.g., crash recovery),
    LangGraph will resume from the last successful checkpoint automatically.
    
    Args:
        lead_id: UUID of the Lead record in PostgreSQL
        workflow_id: Unique ID for this execution run
        lead_payload: The raw lead data (email, name, budget, etc.)
    """
    from app.core.logging_config import set_correlation_id
    set_correlation_id(workflow_id)  # Inject into all logs for this execution

    logger.info(f"Starting lead workflow: lead={lead_id}, workflow={workflow_id}")

    try:
        graph_app = build_graph()
    except ImportError as e:
        logger.error(f"Cannot build graph: {e}")
        return

    # Initial state — the lead payload lives in memory for the nodes to read
    initial_state = AgentState(
        lead_id=uuid.UUID(lead_id),
        workflow_id=uuid.UUID(workflow_id),
        memory=lead_payload,  # Raw lead data accessible to all nodes
    )

    # The thread_id makes this workflow resumable on crash
    config = {"configurable": {"thread_id": workflow_id}}

    # Stream events to log each node as it completes
    try:
        for event in graph_app.stream(initial_state, config=config):
            for node_name, state_update in event.items():
                logger.info(f"[GRAPH] Node '{node_name}' completed.")
    except Exception as e:
        logger.error(f"Workflow {workflow_id} failed with unhandled exception: {e}", exc_info=True)
