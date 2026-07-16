"""
app/schemas/lead.py
--------------------
Pydantic v2 schemas for the Lead API layer.

Key design principle: Schemas are DECOUPLED from ORM models.
  - LeadCreateRequest: what the API client sends IN
  - LeadResponse: what the API sends OUT
  - These never expose internal DB fields like is_deleted, audit_logs, etc.

This protects against:
  - Over-posting attacks (client sending unexpected fields)
  - Data leakage (exposing internal fields to clients)
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any, Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ─────────────────────────────────────────────────────────────
# REQUEST SCHEMAS (what comes IN from the client)
# ─────────────────────────────────────────────────────────────

class LeadCreateRequest(BaseModel):
    """Validated payload for creating a new lead via POST /v1/leads/"""

    email: EmailStr = Field(
        ...,   # Required field
        description="Primary contact email. Must be a valid email format.",
        examples=["ceo@techcorp.com"]
    )
    first_name: Optional[str] = Field(None, max_length=100, description="Lead's first name")
    last_name: Optional[str] = Field(None, max_length=100, description="Lead's last name")
    phone: Optional[str] = Field(None, max_length=50, description="Contact phone number")
    company: Optional[str] = Field(None, max_length=200, description="Company name")
    job_title: Optional[str] = Field(None, max_length=200, description="Job title of the contact")
    budget: float = Field(
        default=0.0,
        ge=0.0,   # Must be >= 0
        description="Estimated deal budget in USD. Used for priority scoring.",
    )


class LeadPriorityUpdateRequest(BaseModel):
    """Payload for PATCH /v1/leads/{id}/priority — manually assign a priority."""
    priority: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        ..., description="The priority to assign. Only valid on a currently UNASSIGNED lead."
    )


class LoginRequest(BaseModel):
    """Credentials for the POST /v1/auth/login endpoint."""
    username: str = Field(..., description="Username (e.g., admin_user)")
    password: str = Field(..., description="Plaintext password")


class RefreshTokenRequest(BaseModel):
    """Payload for POST /v1/auth/refresh to get a new access token."""
    refresh_token: str = Field(..., description="A valid, non-expired refresh token")


# ─────────────────────────────────────────────────────────────
# RESPONSE SCHEMAS (what goes OUT to the client)
# ─────────────────────────────────────────────────────────────

class LeadResponse(BaseModel):
    """
    Outbound API response for a single lead.
    Only exposes fields that are safe and useful for the API consumer.
    """
    # model_config allows Pydantic to read data from SQLAlchemy ORM objects
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    company: Optional[str]
    priority: str   # Serialized from LeadPriority enum
    created_at: datetime


class WorkflowStatusResponse(BaseModel):
    """Response showing the current processing status of a lead's workflow."""
    model_config = ConfigDict(from_attributes=True)

    lead_id: uuid.UUID
    status: str
    retry_count: int
    last_error: Optional[str]
    updated_at: datetime


class TokenResponse(BaseModel):
    """Response for successful authentication."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class PaginatedLeadResponse(BaseModel):
    """Response for paginated lead list queries."""
    total: int
    page: int
    page_size: int
    items: List[LeadResponse]


class AuditLogResponse(BaseModel):
    """Response showing a single audit log entry for a lead's process."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    action_type: str
    tool_inputs: Optional[Dict[str, Any]] = None
    tool_outputs: Optional[Dict[str, Any]] = None
    llm_reasoning: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    created_at: datetime


# ─────────────────────────────────────────────────────────────
# SCHEDULED EMAIL SCHEMAS
# ─────────────────────────────────────────────────────────────

class ScheduleEmailRequest(BaseModel):
    """Payload for scheduling a follow-up email to a lead at a future time."""
    subject: str = Field(..., min_length=1, max_length=500)
    body: str = Field(..., min_length=1, max_length=10000)
    scheduled_at: datetime = Field(
        ...,
        description="When to send (ISO 8601). Include a timezone offset, e.g. "
                    "2026-08-01T09:30:00+05:30; it is normalized to UTC server-side.",
    )
    # Optional override; defaults to the lead's own email if omitted.
    to_email: Optional[EmailStr] = Field(
        default=None,
        description="Recipient. Defaults to the lead's stored email if not given.",
    )


class ScheduledEmailResponse(BaseModel):
    """Outbound view of a scheduled email."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    to_email: str
    subject: str
    scheduled_at: datetime
    status: str
    attempts: int
    last_error: Optional[str] = None
    sent_at: Optional[datetime] = None
    created_at: datetime


# ─────────────────────────────────────────────────────────────
# BULK IMPORT SCHEMAS
# ─────────────────────────────────────────────────────────────

class PasteImportRequest(BaseModel):
    """Payload for importing leads from pasted text (CSV or JSON)."""
    format: Literal["csv", "json"] = Field(..., description="Format of `data`.")
    data: str = Field(..., min_length=1, description="Raw CSV (with header row) or JSON (array of objects).")


class ImportRowError(BaseModel):
    """One row that couldn't be imported, with a human-readable reason."""
    row: int = Field(..., description="1-based row number in the source (data rows, header excluded).")
    email: Optional[str] = None
    reason: str


class ImportSummaryResponse(BaseModel):
    """Outcome of a bulk import — partial success is normal and fully reported."""
    total: int = Field(..., description="Rows found in the source.")
    created: int
    skipped_duplicates: int
    errors: int
    created_ids: List[uuid.UUID] = Field(default_factory=list)
    error_details: List[ImportRowError] = Field(default_factory=list)

