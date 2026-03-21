"""
Weekly report generator — runs Sunday night.
Produces a leadership-level summary: top accounts, sales trend,
inventory status, any issues from the week.
"""
import logging
from datetime import date, timedelta

import anthropic

from app.config import get_settings
from app.ai.model_router import model_for

log = logging.getLogger(__name__)
settings = get_settings()


async def generate_weekly_report(
    company_id: str,
    company_name: str,
    preferred_language: str,
    data_summary: str,
) -> str:
    """Generate the weekly leadership summary."""
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    today = date.today()
    week_start = today - timedelta(days=today.weekday() + 7)
    week_end = week_start + timedelta(days=6)

    prompt = f"""Generate a weekly business report for {company_name}.
Week: {week_start.isoformat()} to {week_end.isoformat()}

Include:
1. Sales summary (total revenue, top accounts, vs prior week)
2. Inventory status (any critical or low items)
3. Account health changes (any accounts moved to at-risk or dormant)
4. Open issues / anomalies detected
5. Recommended actions for next week (max 3 bullets)

Keep it under 300 words. Executive-level — no fluff.
Language: {preferred_language}

Business data:
{data_summary}"""

    response = await client.messages.create(
        model=model_for("weekly_report"),
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
