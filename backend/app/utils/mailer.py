"""
Outbound system email (SendGrid) — the one place automatic emails are sent.

Every email the system sends on its own (morning briefings, stock alerts,
invitations, password resets) goes through send_system_email(), which records
the send — or the failed attempt — in the audit trail. Message bodies are not
stored in the audit row; the recipient, subject and kind of email are.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings
from app.utils.audit import write_audit_event
from app.utils.safe import is_valid_email, log_safe

log = logging.getLogger(__name__)



def email_configured() -> bool:
    return bool(get_settings().sendgrid_api_key)


def send_system_email(
    db,
    *,
    company_id: Optional[str],
    to: str,
    subject: str,
    text: Optional[str] = None,
    html: Optional[str] = None,
    kind: str,
    actor: str = "system",
) -> bool:
    """
    Send one email and audit it. Returns True only if SendGrid accepted it.

    `kind` names the email for the audit trail, e.g. 'morning_briefing',
    'low_stock_alert', 'user_invite', 'password_reset'.
    """
    settings = get_settings()
    to = (to or "").strip()

    def _audit(event_type: str, **extra) -> None:
        if not company_id or db is None:
            return
        write_audit_event(
            db, company_id=company_id, event_type=event_type, actor_id=actor,
            action_class="commit", capability="email",
            metadata={"kind": kind, "to": to, "subject": subject[:200], **extra},
        )

    if not is_valid_email(to):
        log.warning("System email '%s' not sent: invalid recipient", log_safe(kind))
        _audit("email_send_failed", error="invalid recipient")
        return False
    if not settings.sendgrid_api_key:
        log.warning("System email '%s' not sent: SENDGRID_API_KEY is not set", log_safe(kind))
        _audit("email_send_failed", error="email delivery is not configured")
        return False

    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=settings.from_email,
            to_emails=to,
            subject=subject,
            plain_text_content=text if html is None else None,
            html_content=html,
        )
        response = SendGridAPIClient(settings.sendgrid_api_key).send(message)
        status_code = getattr(response, "status_code", None)
    except Exception as exc:
        log.error("System email '%s' failed for company %s: %s", log_safe(kind), log_safe(company_id), log_safe(exc))
        _audit("email_send_failed", error=str(exc)[:300])
        return False

    _audit("email_sent", status_code=status_code)
    return True
