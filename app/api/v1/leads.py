"""
app/api/v1/leads.py
--------------------
Lead management API endpoints.

Architecture decisions:
  - POST /leads → Returns 202 Accepted immediately.
    The LangGraph workflow runs as a BackgroundTask, so the HTTP response
    is never blocked by AI processing (which can take 2-10 seconds).
    
  - GET /leads/{id} → Returns current status and priority for polling.
    Frontends can poll this endpoint to track workflow progress.
    
  - GET /leads/ → Paginated list of all leads (Admin + Sales only).

Role requirements per endpoint:
  POST /leads    → All authenticated users (Admin, Sales, Operator)
  GET /leads/{id} → Admin + Sales
  GET /leads/    → Admin + Sales
  DELETE /leads/{id} → Admin only (soft delete)
"""
import uuid
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, allow_all_roles, allow_sales_or_admin, allow_admin_only
from app.core.config import settings
from app.models.lead import Lead, WorkflowState, WorkflowStatus, AuditLog
from app.schemas.lead import LeadCreateRequest, LeadResponse, PaginatedLeadResponse, WorkflowStatusResponse, AuditLogResponse

router = APIRouter(prefix="/v1/leads", tags=["Leads"])


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

@router.post(
    "/",
    response_model=LeadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a new lead and start autonomous processing",
    responses={
        202: {"description": "Lead accepted. Processing has started asynchronously."},
        401: {"description": "Missing or invalid JWT token."},
        409: {"description": "Lead with this email already exists."},
        422: {"description": "Validation error (e.g., invalid email format)."},
    },
)
def ingest_lead(
    payload: LeadCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_all_roles),  # Any authenticated user can submit leads
):
    """
    Ingests a new lead and launches the LangGraph autonomous workflow in the background.
    
    The response returns immediately (202 Accepted) so clients don't wait.
    Use GET /leads/{id}/status to track the workflow progress.
    """
    # ── Deduplication Check ───────────────────────────
    existing = db.query(Lead).filter(
        Lead.email == payload.email,
        Lead.is_deleted == False,
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A lead with email '{payload.email}' already exists (ID: {existing.id}).",
        )

    # ── Create Lead Record ────────────────────────────
    new_lead = Lead(
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=payload.phone,
        company=payload.company,
        job_title=payload.job_title,
        budget=payload.budget,
    )
    db.add(new_lead)
    db.flush()  # Flush to generate the UUID from PostgreSQL before commit

    # ── Initialize Workflow State ─────────────────────
    wf_state = WorkflowState(
        lead_id=new_lead.id,
        current_status=WorkflowStatus.RECEIVED,
    )
    db.add(wf_state)
    db.commit()
    db.refresh(new_lead)

    # ── Launch Autonomous Agent ───────────────────────
    # BackgroundTasks runs AFTER the HTTP response is sent.
    # The client gets their 202 immediately while the AI works in the background.
    workflow_id = str(uuid.uuid4())

    # Import here to avoid circular imports
    from app.agents.graph import process_lead
    background_tasks.add_task(
        process_lead,
        lead_id=str(new_lead.id),
        workflow_id=workflow_id,
        lead_payload=payload.model_dump(),  # Passes the full payload as the initial memory
    )

    return LeadResponse(
        id=new_lead.id,
        email=new_lead.email,
        first_name=new_lead.first_name,
        last_name=new_lead.last_name,
        company=new_lead.company,
        priority=new_lead.priority.value,
        created_at=new_lead.created_at,
    )


@router.post(
    "/raw",
    response_model=LeadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    include_in_schema=False,  # hidden — demo seam, not a public contract
    summary="[DEMO-ONLY] Ingest an unvalidated raw lead payload",
)
async def ingest_lead_raw(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_all_roles),
    x_demo_raw: str = Header(default=""),
):
    """
    DEMO-ONLY SEAM — env-guarded and INERT by default.

    Bypasses the strict LeadCreateRequest schema (which requires a valid email)
    so we can demonstrate the missing-contact-info escalation path. The raw JSON
    body is passed straight through to the workflow, so the graph's `validate`
    node sees the payload exactly as sent (e.g. WITHOUT an email) and escalates.

    Guard: active ONLY when BOTH are true:
      - settings.ENVIRONMENT == "development", AND
      - request carries header `X-Demo-Raw: true`
    Otherwise it responds 404, i.e. behaves as if the route does not exist.

    Because the `leads` table requires a non-null unique email, we persist the
    Lead row with a synthetic placeholder email when one is absent — but the
    workflow still receives the ORIGINAL (emailless) payload, so validation
    fails authentically.
    """
    if settings.ENVIRONMENT != "development" or x_demo_raw.strip().lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")

    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")

    raw_email = (body.get("email") or "").strip()
    # Placeholder only to satisfy the NOT NULL/unique DB constraint. The workflow
    # receives `body` (which may have no email) and will escalate on validation.
    db_email = raw_email or f"missing-{uuid.uuid4().hex[:8]}@demo.invalid"

    new_lead = Lead(
        email=db_email,
        first_name=body.get("first_name"),
        last_name=body.get("last_name"),
        phone=body.get("phone"),
        company=body.get("company"),
        job_title=body.get("job_title"),
        budget=body.get("budget") or 0.0,
    )
    db.add(new_lead)
    db.flush()

    db.add(WorkflowState(lead_id=new_lead.id, current_status=WorkflowStatus.RECEIVED))
    db.commit()
    db.refresh(new_lead)

    workflow_id = str(uuid.uuid4())
    from app.agents.graph import process_lead
    background_tasks.add_task(
        process_lead,
        lead_id=str(new_lead.id),
        workflow_id=workflow_id,
        lead_payload=body,  # RAW payload — may lack email → validate node escalates
    )

    return LeadResponse(
        id=new_lead.id,
        email=new_lead.email,
        first_name=new_lead.first_name,
        last_name=new_lead.last_name,
        company=new_lead.company,
        priority=new_lead.priority.value,
        created_at=new_lead.created_at,
    )


@router.get(
    "/{lead_id}/status",
    response_model=WorkflowStatusResponse,
    summary="Check the current workflow status of a lead",
    responses={
        401: {"description": "Missing or invalid JWT token."},
        403: {"description": "Insufficient permissions (requires Sales or Admin role)."},
        404: {"description": "Lead not found."},
    },
)
def get_lead_status(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Returns the current workflow status and retry count for a lead.
    Poll this endpoint to track progress (RECEIVED → ANALYZING → COMPLETED or ESCALATED).
    """
    wf_state = db.query(WorkflowState).filter(WorkflowState.lead_id == lead_id).first()

    if not wf_state:
        raise HTTPException(status_code=404, detail=f"No workflow found for lead ID: {lead_id}")

    return WorkflowStatusResponse(
        lead_id=lead_id,
        status=wf_state.current_status.value,
        retry_count=wf_state.retry_count,
        last_error=wf_state.last_error,
        updated_at=wf_state.updated_at,
    )


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Get a single lead by ID",
    responses={
        401: {"description": "Missing or invalid JWT token."},
        403: {"description": "Insufficient permissions (requires Sales or Admin role)."},
        404: {"description": "Lead not found."},
    },
)
def get_lead(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """Returns the full lead record by ID."""
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_deleted == False).first()

    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found: {lead_id}")

    return LeadResponse(
        id=lead.id,
        email=lead.email,
        first_name=lead.first_name,
        last_name=lead.last_name,
        company=lead.company,
        priority=lead.priority.value,
        created_at=lead.created_at,
    )


@router.get(
    "/",
    response_model=PaginatedLeadResponse,
    summary="List all leads with pagination",
    responses={
        401: {"description": "Missing or invalid JWT token."},
        403: {"description": "Insufficient permissions (requires Sales or Admin role)."},
    },
)
def list_leads(
    page: int = Query(default=1, ge=1, description="Page number (starts at 1)"),
    page_size: int = Query(default=20, ge=1, le=100, description="Number of results per page"),
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Returns a paginated list of all active (non-deleted) leads.
    Ordered by creation date (newest first).
    """
    offset = (page - 1) * page_size

    query = db.query(Lead).filter(Lead.is_deleted == False)
    total = query.count()
    leads = query.order_by(Lead.created_at.desc()).offset(offset).limit(page_size).all()

    items = [
        LeadResponse(
            id=lead.id,
            email=lead.email,
            first_name=lead.first_name,
            last_name=lead.last_name,
            company=lead.company,
            priority=lead.priority.value,
            created_at=lead.created_at,
        )
        for lead in leads
    ]

    return PaginatedLeadResponse(total=total, page=page, page_size=page_size, items=items)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a lead (Admin only)",
    responses={
        401: {"description": "Missing or invalid JWT token."},
        403: {"description": "Only Admins can delete leads."},
        404: {"description": "Lead not found."},
    },
)
def delete_lead(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_admin_only),  # Only Admins can delete
):
    """
    Soft-deletes a lead. The record is marked as deleted (is_deleted=True)
    but remains in the database for audit trail purposes.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_deleted == False).first()

    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found: {lead_id}")

    # Use the soft_delete method from BaseModel
    lead.soft_delete()
    db.commit()
    # 204 No Content — no response body needed


@router.get(
    "/{lead_id}/audit",
    response_model=List[AuditLogResponse],
    summary="Get the audit trail / agent log for a lead",
    responses={
        401: {"description": "Missing or invalid JWT token."},
        403: {"description": "Insufficient permissions (requires Sales or Admin role)."},
        404: {"description": "Lead not found."},
    },
)
def get_lead_audit_logs(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Returns the full, chronological list of audit logs for a lead.
    Useful for displaying the AI agent's reasoning steps and tool executions.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_deleted == False).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found: {lead_id}")

    logs = db.query(AuditLog).filter(AuditLog.lead_id == lead_id).order_by(AuditLog.created_at.asc()).all()
    return logs

