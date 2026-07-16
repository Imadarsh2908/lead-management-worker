"""
app/core/mailer.py
------------------
SMTP email sending, with the same resilience posture as the rest of the app:
retry transient failures, a circuit breaker to protect a struggling SMTP server,
and a graceful DRY-RUN mode so local/dev/demo runs never need real mail creds.

This is the Python equivalent of a "nodemailer" transport — stdlib smtplib does
the sending; no third-party mail library required.

Failure model (mirrors resilience.py): send_email() RAISES on failure so the
caller (the scheduler dispatcher) owns retry counting, status transitions, and
the audit trail. Transient network errors are retried here with backoff;
deterministic errors (auth failure, recipient refused) propagate immediately —
retrying them just wastes attempts.
"""
import smtplib
from email.message import EmailMessage

from loguru import logger
from pybreaker import CircuitBreaker, CircuitBreakerError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.core.config import settings

# Infrastructure protection (not a business constant, so intentionally NOT in
# policy.yaml — same rationale as db_breaker in resilience.py). Opens after 5
# consecutive SMTP failures, tries again after 60s.
smtp_breaker = CircuitBreaker(fail_max=5, reset_timeout=60)

# Transient transport errors worth retrying. Auth/recipient errors are NOT here:
# they are deterministic and should surface to the caller on the first attempt.
_RETRYABLE_SMTP_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPHeloError,
    ConnectionError,
    TimeoutError,
    OSError,
)


def _build_message(to_email: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


@smtp_breaker
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_RETRYABLE_SMTP_ERRORS),
    reraise=True,
)
def _smtp_send(msg: EmailMessage) -> None:
    """Opens an SMTP connection and sends one message. Retried on transient errors."""
    if settings.SMTP_USE_TLS:
        # STARTTLS flow (typically port 587).
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value())
            server.send_message(msg)
    else:
        # Implicit SSL flow (typically port 465).
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD.get_secret_value())
            server.send_message(msg)


def send_email(to_email: str, subject: str, body: str) -> str:
    """
    Send an email. Returns a short delivery-mode string ("sent" | "dry_run").

    Raises on a real send failure (SMTP error, breaker open) so the caller can
    record the failure and decide whether to retry later. In DRY-RUN mode
    (EMAIL_ENABLED=False) it never raises — it just logs and returns "dry_run".
    """
    if not settings.EMAIL_ENABLED:
        logger.info(
            f"[MAILER] DRY-RUN (EMAIL_ENABLED=false) — would send to={to_email!r} "
            f"subject={subject!r} ({len(body)} chars). Set EMAIL_ENABLED=true + SMTP_* to send for real."
        )
        return "dry_run"

    msg = _build_message(to_email, subject, body)
    try:
        _smtp_send(msg)
    except CircuitBreakerError as e:
        logger.error(f"[MAILER] SMTP circuit breaker OPEN — not attempting send to {to_email}: {e}")
        raise
    logger.info(f"[MAILER] Email sent to {to_email} (subject={subject!r}).")
    return "sent"
