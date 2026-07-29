"""
Email action handlers — draft and send emails via SendGrid.
Direct sends require 'notify' level (pre-approved alert types).
Customer-facing emails always go through draft_and_wait.
"""
import re

import sendgrid
from sendgrid.helpers.mail import Mail

from app.config import get_settings

settings = get_settings()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _recipient_allowed(to_email: str) -> tuple[bool, str]:
    """Validate an auto-alert recipient: well-formed address + optional domain
    allow-list. Prevents an unvalidated payload from emailing arbitrary addresses."""
    if not _EMAIL_RE.match(to_email):
        return False, "Invalid recipient address"
    allow = [d.strip().lower() for d in (settings.alert_email_allowlist or "").split(",") if d.strip()]
    if allow:
        domain = to_email.rsplit("@", 1)[-1].lower()
        if domain not in allow:
            return False, "Recipient domain not permitted for alert emails"
    return True, ""


async def send_alert_email(payload: dict, company_id: str) -> dict:
    """Send a pre-approved alert email (NOTIFY level)."""
    to_email = payload.get("to_email")
    subject = payload.get("subject", "SecretaryAI Alert")
    body = payload.get("body", "")

    if not to_email:
        return {"status": "error", "message": "No recipient specified"}

    ok, reason = _recipient_allowed(str(to_email))
    if not ok:
        return {"status": "error", "message": reason}

    sg = sendgrid.SendGridAPIClient(api_key=settings.sendgrid_api_key)
    message = Mail(
        from_email=settings.from_email,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )

    try:
        response = sg.send(message)
        return {"status": "sent", "status_code": response.status_code}
    except Exception as e:
        return {"status": "error", "message": str(e)}


async def create_draft_email(payload: dict) -> dict:
    """Create a draft email for user review (DRAFT_AND_WAIT level)."""
    return {
        "type": "email_draft",
        "to": payload.get("to", ""),
        "subject": payload.get("subject", ""),
        "body": payload.get("body", ""),
        "note": "Review and edit before sending. SecretaryAI will not send this automatically.",
    }
