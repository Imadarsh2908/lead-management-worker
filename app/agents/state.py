"""
app/agents/state.py
--------------------
The canonical AgentState Pydantic model passed between LangGraph nodes.

Design decisions:
  - Using Pydantic v2 BaseModel (not TypedDict) for runtime validation.
    Any invalid state mutations during node execution fail loudly rather than silently.
  - validate_assignment=True: even after initial creation, assigning a wrong type
    to a field raises a ValidationError immediately.
  - Fields are intentionally flat — deeply nested dicts are kept in `memory`
    to avoid excessive Pydantic complexity at the graph level.
"""
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field, ConfigDict


# ─────────────────────────────────────────────────────────────
# Sub-schemas for complex state fields
# ─────────────────────────────────────────────────────────────

class ToolCallRecord(BaseModel):
    """
    Records every external tool invocation.
    Stored in tool_history list so the LLM can see what it already tried,
    preventing it from calling the same failing tool in a loop.
    """
    tool_name: str
    inputs: Dict[str, Any]
    outputs: Optional[Dict[str, Any]] = None
    error: Optional[str] = None       # Set if the tool raised an exception
    success: bool = True
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuditLogEntry(BaseModel):
    """
    An in-memory audit entry collected during execution.
    Flushed to PostgreSQL in bulk when the workflow reaches the final audit node.
    """
    action_type: str    # Maps to AuditActionType enum values
    message: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────
# MAIN STATE OBJECT
# ─────────────────────────────────────────────────────────────

class AgentState(BaseModel):
    """
    The single source of truth for the LangGraph workflow execution.
    
    This object is:
      - Created fresh when a new lead is received
      - Serialized and saved to Redis after every node (checkpointing)
      - Deserialized from Redis to resume a crashed workflow
      - Written to PostgreSQL at the end of the workflow (audit node)
    """
    # Validate assignments after model creation — catches bugs where a node
    # tries to set an invalid status or a confidence score > 1.0
    model_config = ConfigDict(validate_assignment=True)

    # ── Identifiers ──────────────────────────────────────
    # workflow_id: unique per execution run (a re-processed lead gets a NEW workflow_id)
    # lead_id: links back to the Lead PostgreSQL record
    workflow_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    lead_id: uuid.UUID

    # ── Flow Control ─────────────────────────────────────
    # status: high-level business state — synced to the DB for dashboard visibility
    status: str = Field(default="RECEIVED")
    
    # current_step: the exact LangGraph node name currently executing
    # Used by conditional edge functions to make routing decisions
    current_step: str = Field(default="receive_lead")
    
    # next_action: the LLM sets this to signal its decision to the routing edges
    # e.g., "generate_follow_up", "escalate", "notify"
    next_action: Optional[str] = Field(default=None)
    
    # retry_count: incremented by the retry node, checked by routing edges
    # If retry_count >= 3, the circuit forces escalation instead of more retries
    retry_count: int = Field(default=0, ge=0)

    # ── AI Outputs ────────────────────────────────────────
    # priority: LLM's final classification (HIGH, MEDIUM, LOW, SPAM, UNASSIGNED)
    priority: str = Field(default="UNASSIGNED")
    
    # confidence: 0.0 to 1.0, set by the LLM alongside its priority decision
    # If < 0.70, the routing edge overrides next_action and forces escalation
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # ── Validation Results ────────────────────────────────
    # Populated by the validate node with human-readable error descriptions
    # e.g., ["Missing email address", "Phone number format invalid"]
    validation_errors: List[str] = Field(default_factory=list)

    # ── Context & Scratchpad ─────────────────────────────
    # memory: ephemeral working scratchpad for the LLM
    # Contains raw lead payload, enrichment data, and intermediate computations
    # Cleared or not saved to DB long-term — only tool_history and audit_logs are persisted
    memory: Dict[str, Any] = Field(default_factory=dict)
    
    # ── Observability & Tracing ──────────────────────────
    # All tool calls appended here so the LLM sees what it has already tried
    tool_history: List[ToolCallRecord] = Field(default_factory=list)
    
    # All significant events appended here, flushed to PostgreSQL at the audit node
    audit_logs: List[AuditLogEntry] = Field(default_factory=list)

    # ── Timestamps ────────────────────────────────────────
    # Used to compute workflow latency (SLA monitoring)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
