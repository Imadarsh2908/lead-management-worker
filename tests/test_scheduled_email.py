"""
tests/test_scheduled_email.py
-----------------------------
Covers the scheduled-email feature: the schedule/list/cancel API, and the
background dispatcher (app/core/scheduler.dispatch_due_emails).

Notes:
  - ENVIRONMENT=testing (set by conftest) means the APScheduler thread never
    starts; tests call dispatch_due_emails() directly for determinism.
  - EMAIL_ENABLED defaults to False, so the mailer runs in DRY-RUN mode and
    never opens a socket — dispatch still transitions rows to SENT.
  - dispatch_due_emails() opens its own Session on app.core.database.engine,
    so (like test_node_audit) we patch that engine to the test DB.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.lead import Lead, ScheduledEmail, EmailStatus, AuditLog


def _make_lead(db, email="lead@corp.com"):
    lead = Lead(id=uuid.uuid4(), email=email)
    db.add(lead)
    db.commit()
    return lead


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────

def test_schedule_email_defaults_to_lead_email(client, db_session):
    lead = _make_lead(db_session, "target@corp.com")
    resp = client.post(
        f"/v1/leads/{lead.id}/schedule-email",
        json={"subject": "Following up", "body": "Hi there!", "scheduled_at": "2030-01-01T09:00:00+05:30"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["to_email"] == "target@corp.com"   # defaulted from the lead
    assert data["status"] == "PENDING"
    assert data["attempts"] == 0


def test_schedule_email_explicit_recipient(client, db_session):
    lead = _make_lead(db_session, "lead2@corp.com")
    resp = client.post(
        f"/v1/leads/{lead.id}/schedule-email",
        json={"subject": "Hi", "body": "Body", "scheduled_at": "2030-01-01T00:00:00Z",
              "to_email": "Someone.Else@Corp.com"},
    )
    assert resp.status_code == 201
    assert resp.json()["to_email"] == "someone.else@corp.com"  # normalized lowercase


def test_schedule_email_unknown_lead_404(client):
    resp = client.post(
        f"/v1/leads/{uuid.uuid4()}/schedule-email",
        json={"subject": "x", "body": "y", "scheduled_at": "2030-01-01T00:00:00Z"},
    )
    assert resp.status_code == 404


def test_schedule_email_rejects_bad_recipient(client, db_session):
    lead = _make_lead(db_session, "lead3@corp.com")
    resp = client.post(
        f"/v1/leads/{lead.id}/schedule-email",
        json={"subject": "x", "body": "y", "scheduled_at": "2030-01-01T00:00:00Z", "to_email": "not-an-email"},
    )
    assert resp.status_code == 422


def test_list_and_cancel_flow(client, db_session):
    lead = _make_lead(db_session, "lead4@corp.com")
    created = client.post(
        f"/v1/leads/{lead.id}/schedule-email",
        json={"subject": "s", "body": "b", "scheduled_at": "2030-01-01T00:00:00Z"},
    ).json()
    email_id = created["id"]

    listed = client.get(f"/v1/leads/{lead.id}/emails")
    assert listed.status_code == 200
    assert any(e["id"] == email_id for e in listed.json())

    # cancel a PENDING email → CANCELLED
    cancelled = client.delete(f"/v1/leads/{lead.id}/emails/{email_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    # cancelling again → 409 (no longer PENDING)
    again = client.delete(f"/v1/leads/{lead.id}/emails/{email_id}")
    assert again.status_code == 409


# ─────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────

def _schedule_row(db, lead_id, when, to="x@corp.com"):
    row = ScheduledEmail(
        lead_id=lead_id, to_email=to, subject="S", body="B",
        scheduled_at=when, status=EmailStatus.PENDING,
    )
    db.add(row)
    db.commit()
    return row


def test_dispatch_sends_due_email_dry_run(db_session):
    """A due PENDING email is sent (dry-run) and transitions to SENT with an audit row."""
    from app.core.scheduler import dispatch_due_emails
    lead = _make_lead(db_session, "due@corp.com")
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    row = _schedule_row(db_session, lead.id, past, to="due@corp.com")

    with patch("app.core.database.engine", db_session.bind):
        sent = dispatch_due_emails()

    assert sent == 1
    db_session.expire_all()
    refreshed = db_session.query(ScheduledEmail).filter(ScheduledEmail.id == row.id).one()
    assert refreshed.status == EmailStatus.SENT
    assert refreshed.sent_at is not None
    assert refreshed.attempts == 1
    # an audit row was written for the send
    audits = db_session.query(AuditLog).filter(AuditLog.lead_id == lead.id).all()
    assert any("email" in (a.message or "").lower() for a in audits)


def test_dispatch_skips_future_email(db_session):
    from app.core.scheduler import dispatch_due_emails
    lead = _make_lead(db_session, "future@corp.com")
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    row = _schedule_row(db_session, lead.id, future)

    with patch("app.core.database.engine", db_session.bind):
        sent = dispatch_due_emails()

    assert sent == 0
    db_session.expire_all()
    assert db_session.query(ScheduledEmail).filter(ScheduledEmail.id == row.id).one().status == EmailStatus.PENDING


def test_dispatch_failure_marks_failed_after_max_attempts(db_session):
    """A send that raises past EMAIL_MAX_ATTEMPTS lands in FAILED with the error recorded."""
    from app.core.scheduler import dispatch_due_emails
    from app.core.config import settings
    lead = _make_lead(db_session, "fail@corp.com")
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    row = _schedule_row(db_session, lead.id, past)

    with patch.object(settings, "EMAIL_MAX_ATTEMPTS", 1), \
         patch("app.core.database.engine", db_session.bind), \
         patch("app.core.mailer.send_email", side_effect=RuntimeError("smtp boom")):
        sent = dispatch_due_emails()

    assert sent == 0
    db_session.expire_all()
    refreshed = db_session.query(ScheduledEmail).filter(ScheduledEmail.id == row.id).one()
    assert refreshed.status == EmailStatus.FAILED
    assert refreshed.attempts == 1
    assert "smtp boom" in (refreshed.last_error or "")


def test_dispatch_retries_before_failing(db_session):
    """Below EMAIL_MAX_ATTEMPTS a failed send returns to PENDING to retry next tick."""
    from app.core.scheduler import dispatch_due_emails
    from app.core.config import settings
    lead = _make_lead(db_session, "retry@corp.com")
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    row = _schedule_row(db_session, lead.id, past)

    with patch.object(settings, "EMAIL_MAX_ATTEMPTS", 3), \
         patch("app.core.database.engine", db_session.bind), \
         patch("app.core.mailer.send_email", side_effect=RuntimeError("transient")):
        dispatch_due_emails()

    db_session.expire_all()
    refreshed = db_session.query(ScheduledEmail).filter(ScheduledEmail.id == row.id).one()
    assert refreshed.status == EmailStatus.PENDING   # still retryable
    assert refreshed.attempts == 1


# ─────────────────────────────────────────────────────────────
# Mailer
# ─────────────────────────────────────────────────────────────

def test_mailer_dry_run_when_disabled():
    """With EMAIL_ENABLED=False the mailer never touches SMTP and reports dry_run."""
    from app.core.mailer import send_email
    with patch("smtplib.SMTP") as smtp:
        result = send_email("nobody@corp.com", "subj", "body")
    assert result == "dry_run"
    smtp.assert_not_called()
