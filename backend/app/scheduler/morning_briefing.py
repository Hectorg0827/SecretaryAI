"""
Morning briefing generator — runs daily for each active company.
Scheduled via Celery beat.
"""
import logging
from datetime import datetime, timezone

import anthropic
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

from app.config import get_settings
from app.ai.model_router import model_for
from app.ai.system_prompts import MORNING_BRIEFING_PROMPT

settings = get_settings()
log = logging.getLogger(__name__)


async def generate_morning_briefing(
    company_id: str,
    company_name: str,
    preferred_language: str,
    data_summary: str,
    recipient_email: str,
) -> str:
    """Generate and deliver the daily morning briefing."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    prompt = MORNING_BRIEFING_PROMPT.format(
        company_name=company_name,
        preferred_language=preferred_language,
        data_summary=data_summary,
    )

    response = await client.messages.create(
        model=model_for("morning_briefing"),
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    briefing_text = response.content[0].text

    # ── Send via SendGrid ──────────────────────────────────────────────────────
    if settings.sendgrid_api_key and recipient_email:
        try:
            message = Mail(
                from_email=settings.from_email,
                to_emails=recipient_email,
                subject=f"Good morning — your {company_name} briefing",
                html_content=_to_html(briefing_text),
            )
            sg = SendGridAPIClient(settings.sendgrid_api_key)
            sg.send(message)
            log.info("Morning briefing sent to %s for company %s", recipient_email, company_id)
        except Exception as exc:
            log.error("SendGrid delivery failed for %s: %s", company_id, exc)
    else:
        log.warning(
            "Morning briefing generated for %s but not delivered "
            "(SENDGRID_API_KEY or recipient_email not set)",
            company_id,
        )

    # ── Persist to DB ──────────────────────────────────────────────────────────
    try:
        from app.api.deps import get_db
        db = get_db()
        db.table("morning_briefings").insert({
            "company_id": company_id,
            "briefing_text": briefing_text,
            "recipient_email": recipient_email,
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "language": preferred_language,
        }).execute()
    except Exception as exc:
        log.error("Could not store morning briefing for %s: %s", company_id, exc)

    return briefing_text


def _to_html(text: str) -> str:
    """Convert plain-text briefing to simple HTML for email delivery."""
    lines = text.split("\n")
    html_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            html_lines.append("<br>")
        elif stripped.startswith("# "):
            html_lines.append(f"<h2 style='color:#1e293b'>{stripped[2:]}</h2>")
        elif stripped.startswith("## "):
            html_lines.append(f"<h3 style='color:#334155'>{stripped[3:]}</h3>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            html_lines.append(f"<li>{stripped[2:]}</li>")
        else:
            html_lines.append(f"<p style='margin:4px 0'>{stripped}</p>")
    body = "\n".join(html_lines)
    return (
        "<div style='font-family:sans-serif;max-width:600px;color:#1e293b'>"
        f"{body}"
        "</div>"
    )
