"""
app/agents/graph.py
--------------------
The core LangGraph StateGraph — the brain of the Autonomous Lead Management Worker.

Behavioral source of truth: SOUL.md (the worker's identity & inviolable
guardrails). The numeric thresholds those guardrails use (confidence gate,
max retries, …) live in config/policy.yaml and are read via get_policy() —
never hardcode business constants in this file.

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
import threading
import uuid
from typing import Literal

from loguru import logger

from app.agents.state import AgentState, AuditLogEntry, ToolCallRecord
from app.agents.decision_engine import DecisionEngine, LeadContext
from app.agents.llm_scorer import score_lead, ScoringResult
from app.core.memory import get_checkpointer
from app.core.policy import get_policy
from app.core.resilience import safe_enrich_domain, safe_update_crm, validate_required_fields

# Priority strictness ordering. The rule-based guardrail may move a lead to a
# STRICTER (lower-rank) priority than the LLM proposed, but never a higher one.
_PRIORITY_RANK = {"SPAM": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "UNASSIGNED": 2}


def _priority_rank(priority: str) -> int:
    return _PRIORITY_RANK.get(priority, 2)

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
    Checks if this lead already exists in the CRM (our own leads table stands in
    for the CRM here) to prevent duplicate processing.

    We match on email (excluding the current lead_id so a lead never "finds
    itself"). Wrapped in try/except for graceful degradation: if the DB is
    unavailable we assume a NEW lead and record a warning rather than crashing
    the whole workflow.
    """
    from sqlalchemy.orm import Session
    from app.core.database import engine
    from app.models.lead import Lead

    logger.info(f"[GRAPH] CRM lookup for lead {state.lead_id}")
    email = (state.memory.get("email") or "").lower().strip()

    audit_entry = None
    try:
        with Session(engine) as db:
            query = db.query(Lead).filter(Lead.email == email)
            # Exclude the current lead so it doesn't count as its own duplicate.
            query = query.filter(Lead.id != state.lead_id)
            existing = query.first() if email else None
            exists_in_crm = existing is not None
        audit_entry = _audit(
            state, "TOOL_INVOCATION",
            f"CRM lookup complete. Exists: {exists_in_crm}",
            inputs={"email": email},
            outputs={"exists_in_crm": exists_in_crm},
        )
    except Exception as e:
        # Graceful degradation: treat as a new lead and keep the workflow moving.
        logger.warning(f"[GRAPH] CRM lookup DB error for lead {state.lead_id}: {e}. Assuming new lead.")
        exists_in_crm = False
        audit_entry = _audit(
            state, "TOOL_INVOCATION",
            f"CRM lookup degraded (DB error) — assuming new lead. Error: {e}",
            inputs={"email": email},
            outputs={"exists_in_crm": False, "degraded": True},
        )

    return {
        "current_step": "crm_lookup",
        "memory": {**state.memory, "exists_in_crm": exists_in_crm},
        "audit_logs": state.audit_logs + [audit_entry],
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

    failed = bool(enrichment_data.get("enrichment_failed", False))

    # Count enrichment failures on a DEDICATED counter (not the general retry_count).
    # route_after_enrichment uses this to decide retry-vs-degrade, so enrichment can
    # exhaust its attempts and proceed with degraded data WITHOUT tripping the
    # general circuit breaker that escalates to a human.
    enrichment_retry_count = state.enrichment_retry_count + 1 if failed else state.enrichment_retry_count

    # Record the tool call in tool_history so the LLM knows what was called
    tool_record = ToolCallRecord(
        tool_name="enrich_lead_domain",
        inputs={"email": email},
        outputs=enrichment_data,
        success=not failed,
    )

    return {
        "status": "ENRICHING",
        "current_step": "enrichment",
        "enrichment_retry_count": enrichment_retry_count,
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


def _llm_tool_record(result: ScoringResult, context: dict) -> ToolCallRecord:
    """Captures the full LLM exchange as an auditable ToolCallRecord."""
    exchange = result._exchange or {}
    raw = exchange.get("raw_response")
    return ToolCallRecord(
        tool_name="llm_score_lead",
        inputs={
            "context": context,
            "prompt_hash": exchange.get("prompt_hash"),
            "prompt_excerpt": exchange.get("prompt_excerpt"),
        },
        outputs={
            # Cap raw text so a chatty model can't bloat the audit row.
            "raw_response": raw[:2000] if isinstance(raw, str) else raw,
            "parsed": exchange.get("parsed"),
            "priority": result.priority,
            "confidence": result.confidence,
            "next_action": result.next_action,
            "source": result.source,
            "fallback_reason": exchange.get("fallback_reason"),
        },
        success=result.source != "rules_fallback",
    )


def node_lead_score(state: AgentState) -> dict:
    """
    NODE: lead_score
    Scores the enriched lead with the REAL LLM (app.agents.llm_scorer.score_lead),
    then runs the rule-based DecisionEngine as a GUARDRAIL on top of the model's
    output.

    Guardrail contract (see previous phase — routing semantics unchanged):
      - Rules may only make the outcome STRICTER: downgrade priority or force
        escalation. They may NEVER upgrade priority or grant a more autonomous
        action than the model asked for.
      - Every override emits its own "GUARDRAIL_OVERRIDE" audit entry with the
        rule name and before/after values.

    The LLM's confidence flows into state.confidence, so the existing confidence
    gate in route_after_decision keeps its behavior (a rules_fallback scores 0.50,
    which is below the confidence gate and therefore escalates by design).
    """
    logger.info(f"[GRAPH] Scoring lead {state.lead_id}")
    enrichment = state.memory.get("enrichment", {})

    # Build the context dict handed to the model (and, below, the guardrail).
    context = {
        "email": state.memory.get("email"),
        "budget": state.memory.get("budget", 0.0),
        "job_title": state.memory.get("job_title", ""),
        "company": state.memory.get("company"),
        "company_size": enrichment.get("company_size"),
        "is_freemail": enrichment.get("is_freemail", False),
        "enrichment_failed": enrichment.get("enrichment_failed", False),
    }

    # 1. LLM proposes a score (self-corrects / falls back internally).
    llm_result = score_lead(context)

    # 2. Rule engine as guardrail — feed the LLM's confidence so its low-confidence
    #    guardrail evaluates against the model's actual certainty.
    guardrail_ctx = LeadContext(
        email=context["email"],
        budget=context["budget"] or 0.0,
        job_title=context["job_title"] or "",
        ai_confidence=llm_result.confidence,
        company_size=context["company_size"],
        is_freemail=context["is_freemail"],
    )
    rules = DecisionEngine().process_lead(guardrail_ctx)

    overrides = []
    final_priority = llm_result.priority
    final_confidence = llm_result.confidence

    # GUARDRAIL A: rules may DOWNGRADE priority (stricter), never upgrade it.
    if _priority_rank(rules.priority) < _priority_rank(final_priority):
        overrides.append(_audit(
            state, "GUARDRAIL_OVERRIDE",
            f"Priority downgraded by rule engine: {final_priority} → {rules.priority}",
            rule="PriorityDowngradeGuardrail",
            before=final_priority, after=rules.priority,
        ))
        final_priority = rules.priority

    # GUARDRAIL B: hard-stop rules (missing email / low confidence) FORCE escalate.
    if rules.action in ("ASK_USER", "ESCALATE"):
        final_action = "ESCALATE"
        if llm_result.next_action != "ESCALATE":
            overrides.append(_audit(
                state, "GUARDRAIL_OVERRIDE",
                f"Guardrail forced escalation (rule action={rules.action}).",
                rule="EscalationGuardrail",
                before=llm_result.next_action, after="ESCALATE",
            ))
    else:
        # Derive a routable action from the guardrailed priority — identical to the
        # phase-1 DecisionEngine mapping, so routing semantics are unchanged.
        final_action = "generate_follow_up" if (
            final_priority == "HIGH" or rules.assigned_queue == "SENIOR_SALES"
        ) else "notify"

    return {
        "status": "ANALYZING",
        "current_step": "lead_score",
        "priority": final_priority,
        "confidence": final_confidence,
        "next_action": final_action,
        "memory": {**state.memory, "decision": {
            "llm": llm_result.model_dump(),
            "rules_priority": rules.priority,
            "final_priority": final_priority,
            "final_action": final_action,
        }},
        "tool_history": state.tool_history + [_llm_tool_record(llm_result, context)],
        "audit_logs": state.audit_logs + [
            _audit(
                state, "LLM_REASONING",
                f"LLM scored lead: priority={llm_result.priority}, "
                f"confidence={llm_result.confidence:.2f}, source={llm_result.source}",
                reasoning=llm_result.reasoning,
                confidence=llm_result.confidence,
                source=llm_result.source,
            )
        ] + overrides,
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
    The routing edge checks retry_count against policy.workflow.max_retries and
    routes to escalation once the ceiling is reached.
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
    # Build a human-readable failure reason for the audit trail. Escalation can be
    # triggered by validation errors, low confidence, or exhausted retries — capture
    # whichever applies so the on-call human knows why the lead landed on their desk.
    enrichment = state.memory.get("enrichment", {})
    policy = get_policy()
    if state.validation_errors:
        failure_reason = f"Validation failed: {state.validation_errors}"
    elif state.confidence < policy.decision.confidence_gate:
        failure_reason = (
            f"AI confidence {state.confidence:.0%} below "
            f"{policy.decision.confidence_gate:.0%} threshold"
        )
    elif state.retry_count >= policy.workflow.max_retries:
        failure_reason = f"Max retries ({state.retry_count}) exhausted"
    elif enrichment.get("enrichment_failed"):
        failure_reason = "Enrichment failure"
    else:
        failure_reason = "Unspecified escalation"

    logger.warning(
        f"[GRAPH] Escalating lead {state.lead_id} to human. "
        f"Reason: {failure_reason} (retry_count={state.retry_count})"
    )

    return {
        "status": "ESCALATED",
        "current_step": "escalate",
        "audit_logs": state.audit_logs + [
            _audit(
                state, "ESCALATION",
                "Lead escalated to human agent for manual review.",
                failure_reason=failure_reason,
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

            # 3a. Bulk-insert the reasoning/state-transition audit entries.
            #     (metadata inputs/outputs are carried through when a node set them,
            #      e.g. the CRM lookup node.)
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

            # 3b. Persist tool I/O: one row per ToolCallRecord so tool_inputs /
            #     tool_outputs are actually populated (previously nothing wrote them).
            for tool_call in state.tool_history:
                db.add(AuditLog(
                    lead_id=state.lead_id,
                    action_type="TOOL_INVOCATION",
                    tool_inputs=tool_call.inputs,
                    tool_outputs=tool_call.outputs,
                    llm_reasoning={
                        "tool_name": tool_call.tool_name,
                        "success": tool_call.success,
                        "error": tool_call.error,
                        "workflow_id": str(state.workflow_id),
                    },
                    message=f"Tool '{tool_call.tool_name}' invoked (success={tool_call.success}).",
                ))

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


def route_after_enrichment(state: AgentState) -> Literal["retry", "lead_score"]:
    """
    Decides what to do after the enrichment node runs.

    Enrichment failure is TREATED DIFFERENTLY from validation/confidence failure:
      - It is a degradable, transient condition (the enrichment API being down),
        so we RETRY it (route → "retry") while we still have attempts left.
      - It is NOT, by itself, a reason to escalate to a human.

    Two-way distinction, encoded explicitly against the DEDICATED
    enrichment_retry_count (NOT the general retry_count / circuit breaker):
      1. enrichment_failed AND enrichment_retry_count < max_retries → "retry"
      2. everything else                                            → "lead_score"

    Case (2) covers BOTH a successful enrichment AND the FINAL attempt
    (enrichment_retry_count >= max_retries): on the final attempt we PROCEED to scoring with
    enrichment_failed=True still in memory (graceful degradation) rather than
    escalating. Because this counter is separate from retry_count, exhausting
    enrichment attempts caps the general breaker at 2 loop-backs and NEVER trips
    route_after_retry's escalation — escalation stays reserved for
    validation/confidence failures (route_after_validate / the confidence guardrail).
    """
    enrichment = state.memory.get("enrichment", {})
    enrichment_failed = bool(enrichment.get("enrichment_failed"))
    max_retries = get_policy().workflow.max_retries

    if enrichment_failed and state.enrichment_retry_count < max_retries:
        logger.warning(
            f"[ROUTE] Enrichment failed (enrichment_retry_count={state.enrichment_retry_count}) → retrying"
        )
        return "retry"

    if enrichment_failed:
        # Final attempt: proceed with degraded (null) company data — do NOT escalate.
        logger.warning(
            f"[ROUTE] Enrichment still failing at enrichment_retry_count={state.enrichment_retry_count} "
            "→ proceeding to scoring with degraded data (graceful degradation)"
        )
    return "lead_score"


def route_after_decision(state: AgentState) -> Literal["generate_follow_up", "notify", "escalate"]:
    """
    This is the most critical routing edge — the AI confidence guardrail.

    Even if the Decision Engine decided to "generate_follow_up", if the
    underlying confidence score is too low, we OVERRIDE that decision
    and escalate to a human. The agent's recommendation is advisory,
    not absolute, when confidence is below the threshold.
    """
    # CONFIDENCE GUARDRAIL: override any action if AI isn't sure enough.
    gate = get_policy().decision.confidence_gate
    if state.confidence < gate:
        logger.warning(f"[ROUTE] Confidence {state.confidence:.0%} < {gate:.0%} → escalating")
        return "escalate"

    # Explicit, exhaustive mapping from the engine's action → next node.
    # ASK_USER means we cannot proceed autonomously, so it escalates too.
    # There is deliberately NO catch-all "else → notify": an unknown action is a
    # bug, and we FAIL SAFE (escalate to a human) rather than FAIL OPEN (auto-notify).
    action_to_node: dict[str, Literal["generate_follow_up", "notify", "escalate"]] = {
        "generate_follow_up": "generate_follow_up",
        "notify": "notify",
        "ASK_USER": "escalate",
        "ESCALATE": "escalate",
    }
    next_node = action_to_node.get(state.next_action or "")
    if next_node is None:
        logger.warning(
            f"[ROUTE] Unknown decision action '{state.next_action}' → escalating (fail safe)"
        )
        return "escalate"
    return next_node


def route_after_retry(state: AgentState) -> Literal["escalate", "validate"]:
    """
    If we've hit the configured retry ceiling (policy.workflow.max_retries), we're
    in an infinite failure loop. Break it by escalating to a human rather than
    retrying forever.
    """
    max_retries = get_policy().workflow.max_retries
    if state.retry_count >= max_retries:
        logger.error(f"[ROUTE] Max retries ({state.retry_count}/{max_retries}) reached → escalating")
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
    # NOTE: enrichment → {retry | lead_score} is a CONDITIONAL edge (see below),
    # not a fixed edge — this is what wires the previously-orphaned retry loop.
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
    # Retry loop wiring: on enrichment failure (with attempts remaining) route into
    # the retry node; otherwise proceed to scoring (possibly with degraded data).
    workflow.add_conditional_edges(
        "enrichment",
        route_after_enrichment,
        {
            "retry": "retry",
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


# ── Compiled-graph singleton ──────────────────────────────────
# Compiling the StateGraph is expensive and only needs to happen ONCE per process.
# process_lead() used to call build_graph() on every invocation; now every
# FastAPI background task reuses this lazily-built, thread-safe singleton.
_compiled_graph = None
_compiled_graph_lock = threading.Lock()


def get_compiled_graph():
    """
    Returns the process-wide compiled LangGraph app, building it on first use.

    Uses double-checked locking so concurrent background tasks don't each pay
    the compilation cost (or race to build competing instances). Propagates the
    ImportError from build_graph() when LangGraph is unavailable so callers keep
    their existing fallback behavior.
    """
    global _compiled_graph
    if _compiled_graph is None:
        with _compiled_graph_lock:
            # Re-check inside the lock: another thread may have built it while we waited.
            if _compiled_graph is None:
                _compiled_graph = build_graph()
    return _compiled_graph


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
        graph_app = get_compiled_graph()  # Reuse the process-wide compiled singleton
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
