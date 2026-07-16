"""
app/models/lead.py
-------------------
Database models for the Lead Management Worker.

Tables:
  1. leads           — The core lead contact record
  2. workflow_states — Tracks the AI agent's current state per lead
  3. audit_logs      — Immutable log of every AI decision and tool call

Design principles:
  - Enums are stored as VARCHAR strings (not INT codes) for readability in raw SQL queries
  - JSONB columns store flexible AI outputs without needing schema changes
  - Relationships use cascade="all, delete-orphan" to prevent orphaned rows
  - Indexes are placed on columns commonly used in WHERE clauses
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List, Dict, Any

from sqlalchemy import String, DateTime, Boolean, ForeignKey, Enum, Index, Integer, Float, Uuid, JSON, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import BaseModel

# Dialect-agnostic JSON: compiles to JSONB on PostgreSQL and JSON on SQLite
JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


# ─────────────────────────────────────────────────────────────
# ENUMS
# Using str mixin makes comparison with plain strings work naturally.
# ─────────────────────────────────────────────────────────────

class LeadPriority(str, PyEnum):
    HIGH = "HIGH"          # Budget > 5L or Decision Maker
    MEDIUM = "MEDIUM"      # Promising but not top tier
    LOW = "LOW"            # Low engagement potential
    SPAM = "SPAM"          # Detected as bot/irrelevant
    UNASSIGNED = "UNASSIGNED"  # Not yet scored


class WorkflowStatus(str, PyEnum):
    RECEIVED = "RECEIVED"       # Lead just ingested
    VALIDATING = "VALIDATING"   # Checking required fields
    ENRICHING = "ENRICHING"     # Fetching company data from external APIs
    ANALYZING = "ANALYZING"     # LLM is scoring the lead
    EXECUTING = "EXECUTING"     # Updating CRM and sending notifications
    ESCALATED = "ESCALATED"     # Human intervention required
    COMPLETED = "COMPLETED"     # Workflow finished autonomously
    FAILED = "FAILED"           # Hard failure — max retries exceeded


class AuditActionType(str, PyEnum):
    STATE_TRANSITION = "STATE_TRANSITION"   # Workflow moved from one step to another
    TOOL_INVOCATION = "TOOL_INVOCATION"     # An external API/tool was called
    LLM_REASONING = "LLM_REASONING"         # The LLM made a classification decision
    GUARDRAIL_OVERRIDE = "GUARDRAIL_OVERRIDE"  # A rule guardrail overrode the LLM (downgrade/escalate)
    ESCALATION = "ESCALATION"               # Lead was escalated to human
    SYSTEM_ERROR = "SYSTEM_ERROR"           # An unexpected exception occurred
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"     # A human (Sales/Admin) manually set the priority


class EmailStatus(str, PyEnum):
    PENDING = "PENDING"       # Queued, scheduled_at not yet reached (or not yet claimed)
    SENDING = "SENDING"       # Claimed by a dispatcher worker; send in progress
    SENT = "SENT"             # Delivered to the SMTP server successfully
    FAILED = "FAILED"         # Exhausted max attempts; needs attention
    CANCELLED = "CANCELLED"   # Cancelled before it was sent


# ─────────────────────────────────────────────────────────────
# TABLE 1: Lead
# The primary entity — stores contact info and AI-determined priority.
# ─────────────────────────────────────────────────────────────

class Lead(BaseModel):
    __tablename__ = "leads"

    # Contact Information
    first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Indexed and unique — email is the primary lookup key for deduplication
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    budget: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # AI-determined priority — indexed for fast filtering dashboards
    priority: Mapped[LeadPriority] = mapped_column(
        Enum(LeadPriority, name="lead_priority_enum"),
        default=LeadPriority.UNASSIGNED,
        index=True,
    )

    # ── Relationships ────────────────────────────────────
    # uselist=False: 1-to-1 relationship (one lead has one workflow state at a time)
    workflow_state: Mapped[Optional["WorkflowState"]] = relationship(
        "WorkflowState",
        back_populates="lead",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # One lead generates many audit log entries over its lifetime
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="lead",
        cascade="all, delete-orphan",
    )

    # ── Validation ───────────────────────────────────────
    @validates("email")
    def validate_email(self, key: str, address: str) -> str:
        """Ensures emails are always stored in lowercase and contain '@'."""
        if not address or "@" not in address:
            raise ValueError(f"Invalid email format: {address}")
        return address.lower().strip()

    # Partial index: faster queries when filtering non-deleted leads.
    # Use a text() predicate (not BaseModel.is_deleted, which is the ABSTRACT
    # base's column and cannot bind here) so PostgreSQL compiles the WHERE clause
    # correctly. postgresql_where is dialect-specific — SQLite simply ignores it.
    __table_args__ = (
        Index("ix_leads_active", "id", postgresql_where=text("is_deleted = false")),
    )


# ─────────────────────────────────────────────────────────────
# TABLE 2: WorkflowState
# Tracks exactly where the AI agent is in the processing pipeline.
# Updated after each LangGraph node execution.
# ─────────────────────────────────────────────────────────────

class WorkflowState(BaseModel):
    __tablename__ = "workflow_states"

    # Foreign key linking back to the parent lead
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("leads.id", ondelete="CASCADE"),
        unique=True,   # Enforces 1-to-1: each lead has exactly one workflow state row
        index=True,
        nullable=False,
    )

    # Current position in the state machine
    current_status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, name="workflow_status_enum"),
        default=WorkflowStatus.RECEIVED,
        index=True,
    )

    # Tracks retry attempts to prevent infinite loops
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Stores the last error message for debugging and resume logic
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # ── Relationship ─────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead", back_populates="workflow_state")


# ─────────────────────────────────────────────────────────────
# TABLE 3: AuditLog
# Append-only record of every autonomous decision.
# CRITICAL for debugging AI hallucinations and compliance.
# ─────────────────────────────────────────────────────────────

class AuditLog(BaseModel):
    __tablename__ = "audit_logs"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("leads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # What kind of event this log entry represents
    action_type: Mapped[AuditActionType] = mapped_column(
        Enum(AuditActionType, name="audit_action_type_enum"),
        index=True,
        nullable=False,
    )

    # JSON_TYPE stores arbitrary structured data without schema changes (JSONB on PostgreSQL, JSON on SQLite).
    # These columns allow us to query specific tool failures with GIN indexes.
    tool_inputs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)
    tool_outputs: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)

    # The LLM's chain-of-thought reasoning — vital for understanding AI decisions
    llm_reasoning: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON_TYPE, nullable=True)

    # Human-readable message for quick log scanning
    message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # ── Relationship ─────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead", back_populates="audit_logs")

    # ── Indexes ──────────────────────────────────────────
    # Composite index for the most common query: "all events for a given lead"
    # The GIN index on tool_outputs enables efficient JSONB key/value lookups on
    # PostgreSQL (e.g., "find all logs where tool_outputs->>'status' = 'failed'").
    # On SQLite (used in tests), the GIN index is silently ignored — only pg uses it.
    __table_args__ = (
        Index(
            "ix_audit_logs_lead_id_action_type",
            "lead_id",
            "action_type",
        ),
        Index(
            "ix_audit_logs_tool_outputs_gin",
            "tool_outputs",
            postgresql_using="gin",
        ),
    )


# ─────────────────────────────────────────────────────────────
# TABLE 4: ScheduledEmail
# A follow-up email queued to be sent to a lead at a specific time.
# The background scheduler (app/core/scheduler.py) polls this table and hands
# due rows to the mailer (app/core/mailer.py).
# ─────────────────────────────────────────────────────────────

class ScheduledEmail(BaseModel):
    __tablename__ = "scheduled_emails"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("leads.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )

    # Snapshotted at schedule time so an email still sends correctly even if the
    # lead's contact address is later edited or the lead is soft-deleted.
    to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(String(10000), nullable=False)

    # When the email becomes eligible to send. Stored as NAIVE UTC by convention
    # (not timezone=True): SQLite returns naive datetimes and Postgres returns
    # aware ones, so mixing them breaks comparisons — standardizing on naive-UTC
    # keeps the dispatcher's `scheduled_at <= utcnow()` filter identical on both.
    # The API layer converts any tz-aware input to naive UTC before storing.
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)

    status: Mapped[EmailStatus] = mapped_column(
        Enum(EmailStatus, name="email_status_enum"),
        default=EmailStatus.PENDING,
        index=True,
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # naive UTC

    # ── Relationship ─────────────────────────────────────
    lead: Mapped["Lead"] = relationship("Lead")

    # ── Indexes ──────────────────────────────────────────
    # The dispatcher's hot query is "PENDING rows due now", so index (status, scheduled_at).
    __table_args__ = (
        Index("ix_scheduled_emails_due", "status", "scheduled_at"),
    )
