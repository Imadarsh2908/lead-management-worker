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
from datetime import datetime, timezone
from typing import List

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Header, HTTPException, Query, Request, UploadFile, status,
)
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, allow_all_roles, allow_sales_or_admin, allow_admin_only
from app.core.config import settings
from app.core.lead_import import parse_csv, parse_json, parse_xlsx, parse_inbound_email
from app.models.lead import Lead, WorkflowState, WorkflowStatus, AuditLog, ScheduledEmail, EmailStatus
from app.schemas.lead import (
    LeadCreateRequest, LeadResponse, PaginatedLeadResponse, WorkflowStatusResponse,
    AuditLogResponse, ScheduleEmailRequest, ScheduledEmailResponse,
    PasteImportRequest, ImportSummaryResponse, ImportRowError,
)

router = APIRouter(prefix="/v1/leads", tags=["Leads"])


# ─────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# SHARED INGEST CORE
# Every path that creates a lead — the single POST, and all three bulk import
# sources (file / paste / inbound email) — funnels through here, so dedup and
# workflow-queuing behave identically and never drift.
# ─────────────────────────────────────────────────────────────

class _LeadConflict(Exception):
    """Raised by _create_and_queue_lead when a non-deleted lead already has this email."""
    def __init__(self, existing_id):
        self.existing_id = existing_id
        super().__init__(str(existing_id))


def _create_and_queue_lead(db: Session, background_tasks: BackgroundTasks, data: dict) -> Lead:
    """
    Create a Lead row and queue its autonomous LangGraph workflow.
    Raises _LeadConflict if an active lead with the same email exists.
    """
    existing = db.query(Lead).filter(
        Lead.email == data.get("email"),
        Lead.is_deleted == False,
    ).first()
    if existing:
        raise _LeadConflict(existing.id)

    new_lead = Lead(
        email=data.get("email"),
        first_name=data.get("first_name"),
        last_name=data.get("last_name"),
        phone=data.get("phone"),
        company=data.get("company"),
        job_title=data.get("job_title"),
        budget=data.get("budget") or 0.0,
    )
    db.add(new_lead)
    db.flush()  # generate the UUID before commit
    db.add(WorkflowState(lead_id=new_lead.id, current_status=WorkflowStatus.RECEIVED))
    db.commit()
    db.refresh(new_lead)

    workflow_id = str(uuid.uuid4())
    from app.agents.graph import process_lead  # local import avoids a circular import
    background_tasks.add_task(
        process_lead,
        lead_id=str(new_lead.id),
        workflow_id=workflow_id,
        lead_payload=data,
    )
    return new_lead


def _bulk_ingest(db: Session, background_tasks: BackgroundTasks, rows: list) -> ImportSummaryResponse:
    """
    Validate + create each parsed row, collecting a per-row outcome. Partial
    success is normal: a bad or duplicate row is recorded, never fatal to the batch.
    """
    if len(rows) > settings.MAX_IMPORT_ROWS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many rows ({len(rows)}); the limit is {settings.MAX_IMPORT_ROWS} per import.",
        )

    created_ids, skipped, errors = [], 0, []
    for i, row in enumerate(rows, start=1):
        # Reuse the same schema as the single-lead endpoint — one source of truth
        # for "what a valid lead looks like" (email required + format, budget >= 0, lengths).
        try:
            validated = LeadCreateRequest(**row)
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first.get("loc", ())) or "row"
            errors.append(ImportRowError(row=i, email=row.get("email"), reason=f"{field}: {first.get('msg')}"))
            continue
        try:
            lead = _create_and_queue_lead(db, background_tasks, validated.model_dump())
            created_ids.append(lead.id)
        except _LeadConflict:
            skipped += 1
        except Exception as e:  # noqa: BLE001 — a bad row must not abort the batch
            db.rollback()
            errors.append(ImportRowError(row=i, email=row.get("email"), reason=f"{type(e).__name__}: {e}"[:200]))

    return ImportSummaryResponse(
        total=len(rows),
        created=len(created_ids),
        skipped_duplicates=skipped,
        errors=len(errors),
        created_ids=created_ids,
        error_details=errors,
    )


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
    try:
        new_lead = _create_and_queue_lead(db, background_tasks, payload.model_dump())
    except _LeadConflict as conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A lead with email '{payload.email}' already exists (ID: {conflict.existing_id}).",
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


# ─────────────────────────────────────────────────────────────
# SCHEDULED EMAILS
# Queue a follow-up email to a lead for a future time; the background
# scheduler (app/core/scheduler.py) dispatches it when due.
# ─────────────────────────────────────────────────────────────

def _to_naive_utc(dt: datetime) -> datetime:
    """Normalize an incoming datetime to naive UTC (how scheduled_at is stored)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt  # already naive — assume caller meant UTC


@router.post(
    "/{lead_id}/schedule-email",
    response_model=ScheduledEmailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a follow-up email to a lead at a future time",
    responses={
        404: {"description": "Lead not found."},
        422: {"description": "Validation error (bad email, empty subject/body, etc.)."},
    },
)
def schedule_email(
    lead_id: uuid.UUID,
    payload: ScheduleEmailRequest,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Queues an email in the `scheduled_emails` table with status PENDING. The
    scheduler polls for due rows and sends them (or logs them in dry-run mode
    when EMAIL_ENABLED is false). Recipient defaults to the lead's own email.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_deleted == False).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found: {lead_id}")

    to_email = (payload.to_email or lead.email)
    if not to_email:
        raise HTTPException(status_code=422, detail="No recipient: lead has no email and none was provided.")

    scheduled = ScheduledEmail(
        lead_id=lead.id,
        to_email=to_email.lower().strip(),
        subject=payload.subject,
        body=payload.body,
        scheduled_at=_to_naive_utc(payload.scheduled_at),
        status=EmailStatus.PENDING,
    )
    db.add(scheduled)
    db.commit()
    db.refresh(scheduled)
    return scheduled


@router.get(
    "/{lead_id}/emails",
    response_model=List[ScheduledEmailResponse],
    summary="List scheduled emails for a lead",
    responses={404: {"description": "Lead not found."}},
)
def list_scheduled_emails(
    lead_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """Returns all scheduled emails for a lead, newest scheduled time first."""
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.is_deleted == False).first()
    if not lead:
        raise HTTPException(status_code=404, detail=f"Lead not found: {lead_id}")

    return (
        db.query(ScheduledEmail)
        .filter(ScheduledEmail.lead_id == lead_id)
        .order_by(ScheduledEmail.scheduled_at.desc())
        .all()
    )


@router.delete(
    "/{lead_id}/emails/{email_id}",
    response_model=ScheduledEmailResponse,
    summary="Cancel a still-pending scheduled email",
    responses={
        404: {"description": "Scheduled email not found for this lead."},
        409: {"description": "Email already sent/failed/cancelled — cannot cancel."},
    },
)
def cancel_scheduled_email(
    lead_id: uuid.UUID,
    email_id: uuid.UUID,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Cancels a PENDING scheduled email. Only PENDING emails can be cancelled —
    anything already SENDING/SENT/FAILED/CANCELLED returns 409.
    """
    email = (
        db.query(ScheduledEmail)
        .filter(ScheduledEmail.id == email_id, ScheduledEmail.lead_id == lead_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail=f"Scheduled email not found: {email_id}")
    if email.status != EmailStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel an email in status '{email.status.value}' (only PENDING is cancellable).",
        )

    email.status = EmailStatus.CANCELLED
    db.commit()
    db.refresh(email)
    return email


# ─────────────────────────────────────────────────────────────
# BULK IMPORT — file upload, pasted text, and inbound email
# All three parse their source into rows, then hand off to _bulk_ingest, so
# validation / dedup / workflow-queuing are identical across every source.
# ─────────────────────────────────────────────────────────────

@router.post(
    "/import/file",
    response_model=ImportSummaryResponse,
    summary="Bulk-import leads from a CSV or Excel (.xlsx) file",
    responses={
        200: {"description": "Import processed (see the summary; partial success is normal)."},
        413: {"description": "Too many rows in one import."},
        415: {"description": "Unsupported file type (use .csv or .xlsx)."},
        422: {"description": "File could not be parsed."},
    },
)
async def import_leads_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="A .csv or .xlsx file whose first row is the header."),
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """
    Accepts a spreadsheet, maps its columns to lead fields (email, first_name,
    company, job_title, phone, budget — header names are matched flexibly), and
    creates a lead per row. Returns a summary of created / skipped-duplicate /
    error rows (each error carries its 1-based row number).
    """
    content = await file.read()
    name = (file.filename or "").lower()
    try:
        if name.endswith(".xlsx"):
            rows = parse_xlsx(content)
        elif name.endswith(".csv") or (file.content_type or "").startswith(("text/csv", "application/csv")):
            rows = parse_csv(content.decode("utf-8-sig"))  # utf-8-sig strips Excel's BOM
        else:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Unsupported file type. Upload a .csv or .xlsx file.",
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — bad file bytes / decode / workbook errors
        raise HTTPException(status_code=422, detail=f"Could not parse the file: {e}")

    return _bulk_ingest(db, background_tasks, rows)


@router.post(
    "/import/paste",
    response_model=ImportSummaryResponse,
    summary="Bulk-import leads from pasted CSV or JSON text",
    responses={413: {"description": "Too many rows."}, 422: {"description": "Text could not be parsed."}},
)
def import_leads_paste(
    payload: PasteImportRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    claims: dict = Depends(allow_sales_or_admin),
):
    """Parse pasted CSV (with a header row) or JSON (array of objects) and import each row."""
    try:
        rows = parse_csv(payload.data) if payload.format == "csv" else parse_json(payload.data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"Could not parse {payload.format}: {e}")

    return _bulk_ingest(db, background_tasks, rows)


@router.post(
    "/inbound-email",
    response_model=ImportSummaryResponse,
    include_in_schema=False,  # public webhook, token-guarded — not part of the app's auth'd API surface
    summary="[WEBHOOK] Create a lead from an inbound email (SendGrid/Mailgun Inbound Parse)",
)
async def inbound_email(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_webhook_token: str = Header(default=""),
    token: str = Query(default=""),
):
    """
    WEBHOOK, not a browser endpoint. An inbound-email service (SendGrid or
    Mailgun Inbound Parse) is configured to POST parsed messages here; the
    sender becomes a new lead.

    Security: this is a PUBLIC URL (no JWT), so it is INERT — returns 404 —
    unless INBOUND_EMAIL_TOKEN is configured AND the request presents the
    matching token (header `X-Webhook-Token` or `?token=`). That prevents an
    unconfigured deployment from exposing an open lead-injection endpoint.

    Accepts either JSON or the multipart/form-encoded body the providers send.
    """
    configured = settings.INBOUND_EMAIL_TOKEN.get_secret_value()
    presented = x_webhook_token or token
    if not configured or presented != configured:
        raise HTTPException(status_code=404, detail="Not found")

    if "application/json" in (request.headers.get("content-type", "")):
        payload = await request.json()
    else:
        form = await request.form()
        payload = {k: v for k, v in form.items()}

    row = parse_inbound_email(payload if isinstance(payload, dict) else {})
    if not row.get("email"):
        raise HTTPException(status_code=422, detail="Could not extract a sender email from the inbound message.")

    return _bulk_ingest(db, background_tasks, [row])

