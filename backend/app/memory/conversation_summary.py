"""
Conversation summaries — after each conversation, summarise key points for future context.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


async def summarise_and_save(
    db,
    company_id: str,
    user_id: str,
    conversation_id: str,
    messages: list[dict],
) -> None:
    """Generate a rolling summary of a completed conversation and persist it."""
    if len(messages) < 4:
        return  # Too short to be worth summarising
    import anthropic
    from app.config import get_settings
    from app.ai.model_router import model_for
    import re

    try:
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content'][:300]}" for m in messages[-10:]
        )
        prompt = (
            "Summarise this business conversation in 2-3 sentences. "
            "Focus on: decisions made, accounts discussed, action items. "
            "Also list any account/customer names mentioned.\n\n"
            f"{transcript}\n\n"
            'Reply as JSON: {"summary": "...", "account_names": ["..."]}'
        )
        resp = await client.messages.create(
            model=model_for("classify_intent"),
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        data = json.loads(resp.content[0].text.strip())
        db.table("conversation_summaries").upsert(
            {
                "company_id": company_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
                "summary": data.get("summary", ""),
                "account_names": data.get("account_names", []),
            },
            on_conflict="conversation_id",
        ).execute()
    except Exception as exc:
        log.warning("Conversation summary failed: %s", exc)


def load_recent_summaries(db, company_id: str, user_id: str, limit: int = 5) -> str:
    """Load recent conversation summaries as context for the AI."""
    try:
        rows = (
            db.table("conversation_summaries")
            .select("summary, account_names, created_at")
            .eq("company_id", company_id)
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        if not rows:
            return ""
        lines = []
        for row in rows:
            names = ", ".join(row.get("account_names") or [])
            suffix = f" [{names}]" if names else ""
            lines.append(f"- {row['summary']}{suffix}")
        return "\n".join(lines)
    except Exception as exc:
        log.warning("Failed to load conversation summaries: %s", exc)
        return ""
