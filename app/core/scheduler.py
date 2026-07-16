"""
app/core/scheduler.py
---------------------
Background scheduler that dispatches due scheduled emails.

Design:
  - A single APScheduler interval job (`dispatch_due_emails`) ticks every
    settings.EMAIL_DISPATCH_INTERVAL_SECONDS and sends any ScheduledEmail whose
    scheduled_at has passed. APScheduler provides the "cron"; the queue of work
    lives in Postgres (the scheduled_emails table), so nothing is lost on
    restart and the state is queryable and auditable.

  - MULTI-WORKER SAFETY: under Gunicorn (-w 4) every worker starts its own
    scheduler, so a naive "select due, send" would email a lead up to 4×. We
    guard against that with an OPTIMISTIC CLAIM: before sending, a worker runs
    `UPDATE ... SET status=SENDING WHERE id=:id AND status=PENDING` and only
    proceeds if it changed exactly one row. Whichever worker wins the race owns
    that email; the others' UPDATE affects 0 rows and they skip it. This is
    portable (works on SQLite and Postgres) and needs no SELECT ... FOR UPDATE.

    (In a larger deployment you'd run ONE dedicated scheduler process — or a
    Celery-beat leader — instead of one per web worker. The optimistic claim
    keeps us correct until then.)
"""
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings

_scheduler = None  # module-level singleton (APScheduler BackgroundScheduler)


def _utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime (matches how scheduled_at is stored)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dispatch_due_emails() -> int:
    """
    Send every PENDING email whose scheduled_at has passed. Returns the number
    of emails successfully sent this tick (handy for tests and logging).
    Safe to call directly (tests do) as well as on the scheduler tick.
    """
    from app.core.database import engine
    from app.core.mailer import send_email
    from app.models.lead import ScheduledEmail, EmailStatus, AuditLog

    sent_count = 0
    now = _utcnow_naive()

    with Session(engine) as db:
        due = (
            db.query(ScheduledEmail)
            .filter(ScheduledEmail.status == EmailStatus.PENDING)
            .filter(ScheduledEmail.scheduled_at <= now)
            .order_by(ScheduledEmail.scheduled_at.asc())
            .limit(50)  # bound the batch so one tick can't run unbounded
            .all()
        )

        for email in due:
            # ── Optimistic claim: only ONE worker may transition PENDING→SENDING ──
            claimed = db.execute(
                update(ScheduledEmail)
                .where(ScheduledEmail.id == email.id, ScheduledEmail.status == EmailStatus.PENDING)
                .values(status=EmailStatus.SENDING)
            )
            db.commit()
            if claimed.rowcount != 1:
                continue  # another worker already claimed it; skip

            attempts = email.attempts + 1
            try:
                mode = send_email(email.to_email, email.subject, email.body)
                db.execute(
                    update(ScheduledEmail)
                    .where(ScheduledEmail.id == email.id)
                    .values(status=EmailStatus.SENT, attempts=attempts,
                            sent_at=_utcnow_naive(), last_error=None)
                )
                db.add(AuditLog(
                    lead_id=email.lead_id,
                    action_type="TOOL_INVOCATION",
                    tool_inputs={"to": email.to_email, "subject": email.subject},
                    tool_outputs={"delivery": mode},
                    message=f"Scheduled email {mode} to {email.to_email}.",
                ))
                db.commit()
                sent_count += 1
            except Exception as e:  # noqa: BLE001 — one bad email must not stop the batch
                # Exhausted attempts → FAILED; otherwise back to PENDING to retry next tick.
                final = EmailStatus.FAILED if attempts >= settings.EMAIL_MAX_ATTEMPTS else EmailStatus.PENDING
                db.execute(
                    update(ScheduledEmail)
                    .where(ScheduledEmail.id == email.id)
                    .values(status=final, attempts=attempts, last_error=f"{type(e).__name__}: {e}"[:1000])
                )
                db.add(AuditLog(
                    lead_id=email.lead_id,
                    action_type="SYSTEM_ERROR",
                    message=f"Scheduled email attempt {attempts} failed ({final.value}): {type(e).__name__}",
                ))
                db.commit()
                logger.warning(f"[SCHEDULER] Email {email.id} attempt {attempts} failed → {final.value}: {e}")

    if sent_count:
        logger.info(f"[SCHEDULER] Dispatched {sent_count} due email(s).")
    return sent_count


def start_scheduler() -> None:
    """Start the background scheduler. Called from the FastAPI lifespan hook."""
    global _scheduler
    if settings.ENVIRONMENT == "testing":
        logger.info("[SCHEDULER] Testing environment — scheduler not started (tests call dispatch directly).")
        return
    if not settings.SCHEDULER_ENABLED:
        logger.info("[SCHEDULER] SCHEDULER_ENABLED=false — scheduler not started.")
        return
    if _scheduler is not None:
        return

    from apscheduler.schedulers.background import BackgroundScheduler

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        dispatch_due_emails,
        trigger="interval",
        seconds=settings.EMAIL_DISPATCH_INTERVAL_SECONDS,
        id="dispatch_due_emails",
        max_instances=1,      # never overlap ticks
        coalesce=True,        # if ticks pile up (e.g. after a pause), run once
        next_run_time=datetime.now(timezone.utc),  # run one tick promptly at startup
    )
    _scheduler.start()
    logger.info(
        f"[SCHEDULER] Started — dispatching due emails every "
        f"{settings.EMAIL_DISPATCH_INTERVAL_SECONDS}s."
    )


def stop_scheduler() -> None:
    """Stop the scheduler cleanly on shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[SCHEDULER] Stopped.")
