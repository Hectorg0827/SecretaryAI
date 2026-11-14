"""
Account memory — persist and retrieve facts about specific customers/accounts.
The AI uses this to remember things like "Sunrise complained about shipping delays in Feb"
across conversations.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def load_account_memory(db, company_id: str, account_names: list[str], user_id: str | None = None) -> str:
    """
    Load memory facts for named accounts, return as a formatted string
    suitable for injection into the AI system prompt.
    """
    if not account_names or not user_id:
        return ""
    try:
        rows = (
            db.table("lola_personal_account_memory")
            .select("account_name, facts")
            .eq("company_id", company_id)
            .eq("user_id", user_id)
            .in_("account_name", account_names[:10])
            .execute()
        ).data or []
        if not rows:
            return ""
        parts = []
        for row in rows:
            facts = row.get("facts") or []
            if facts:
                fact_lines = "\n".join(f"  - {f['fact']}" for f in facts[:5])
                parts.append(f"{row['account_name']}:\n{fact_lines}")
        return "\n".join(parts) if parts else ""
    except Exception as exc:
        log.warning("Failed to load account memory: %s", exc)
        return ""


def extract_and_save_memory(db, company_id: str, account_name: str, ai_response: str, user_id: str | None = None) -> None:
    """
    Parse the AI response for durable facts about an account and upsert them.
    Called asynchronously after each conversation turn.
    """
    if not user_id:
        return  # Never persist AI-derived personal facts to legacy company memory.
    import anthropic
    from app.config import get_settings
    from app.ai.model_router import model_for
    import asyncio, json

    async def _extract():
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        prompt = (
            f"Extract 0-3 durable facts about the account '{account_name}' from this AI response. "
            "Only extract facts that would still be useful in future conversations (complaints, preferences, "
            "payment patterns, relationship notes). Ignore transactional data (order totals, dates).\n\n"
            f"Response:\n{ai_response[:1000]}\n\n"
            'Reply ONLY with JSON: [{"fact": "..."}] or [] if nothing durable.'
        )
        try:
            resp = await client.messages.create(
                model=model_for("classify_intent"),
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            new_facts = json.loads(resp.content[0].text.strip())
            if not new_facts:
                return
            # Load existing
            existing = (
                db.table("lola_personal_account_memory")
                .select("facts")
                .eq("company_id", company_id)
                .eq("user_id", user_id)
                .eq("account_name", account_name)
                .maybe_single()
                .execute()
            )
            current_facts = (existing.data or {}).get("facts") or []
            # Merge — deduplicate by fact text
            existing_texts = {f["fact"] for f in current_facts}
            now = datetime.now(timezone.utc).isoformat()
            for nf in new_facts:
                if nf["fact"] not in existing_texts:
                    current_facts.append({"fact": nf["fact"], "updated_at": now})
            # Keep only the 20 most recent facts
            current_facts = current_facts[-20:]
            db.table("lola_personal_account_memory").upsert(
                {"company_id": company_id, "user_id": user_id, "account_name": account_name, "facts": current_facts, "updated_at": now},
                on_conflict="company_id,user_id,account_name",
            ).execute()
        except Exception as exc:
            log.warning("Memory extraction failed for %s: %s", account_name, exc)

    # The chat endpoint already runs inside an async event loop. Never call
    # run_until_complete() or asyncio.run() inside that loop: both raise and
    # silently discard account-memory work. Schedule it instead.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_extract())
    else:
        task = loop.create_task(_extract())
        def _observe(done_task):
            try:
                done_task.result()
            except Exception:
                log.exception("Account memory background extraction failed")
        task.add_done_callback(_observe)
