"""
Morning briefing generator — runs daily for each active company.
Scheduled via Celery beat.
"""
from datetime import datetime, timezone

import anthropic

from app.config import get_settings
from app.ai.model_router import model_for
from app.ai.system_prompts import MORNING_BRIEFING_PROMPT

settings = get_settings()


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

    # TODO: Send via SendGrid and store in DB
    return briefing_text
