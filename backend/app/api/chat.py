"""
Chat API endpoints — conversational AI interface.

Full pipeline:
  1. Load conversation history from Supabase (last N turns)
  2. Classify intent (inventory_check / account_status / sales_report / general_question)
  3. Fetch real QB data matching the intent
  4. Summarize data → inject as context
  5. Stream Claude response via SSE
  6. Persist the new turn to Supabase
  7. Detect any action proposals in the response
"""
import json
import logging
import uuid
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.api.deps import get_adapter, get_db
from app.auth.rbac import get_current_user, require_permission
from app.ai.secretary import classify_intent, stream_chat, chat as ai_chat, detect_action_in_response
from app.ai.data_summarizer import build_query_context
from app.memory.account_memory import load_account_memory, extract_and_save_memory
from app.memory.conversation_summary import load_recent_summaries, summarise_and_save

log = logging.getLogger(__name__)
router = APIRouter()

# Keep at most this many prior turns in the Claude context window
MAX_HISTORY_TURNS = 20
# Characters per turn threshold for trimming (each turn = user + assistant)
MAX_CONTEXT_CHARS = 32_000


# ─── Models ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None

    @field_validator("message")
    @classmethod
    def validate_message_length(cls, v: str) -> str:
        if len(v) > 4000:
            raise ValueError("Message too long (max 4000 characters)")
        return v


class ChatMessage(BaseModel):
    role: str
    content: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_history(db, conversation_id: str, company_id: str, user_id: str) -> list[dict]:
    """Load a user's conversation from the JSONB schema, scoped to tenant AND user.

    The deployed conversations table stores one row per conversation with a
    messages JSONB array, not one row per message. Fail closed on missing rows.
    """
    result = (
        db.table("conversations")
        .select("messages")
        .eq("id", conversation_id)
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return []
    messages = rows[0].get("messages") or []
    if not isinstance(messages, list):
        return []
    return [
        {"role": msg["role"], "content": msg["content"]}
        for msg in messages[-MAX_HISTORY_TURNS * 2:]
        if isinstance(msg, dict)
        and msg.get("role") in ("user", "assistant")
        and isinstance(msg.get("content"), str)
    ]


def _trim_history(history: list[dict]) -> list[dict]:
    """Trim history to stay within context limits, keeping most recent turns."""
    if not history:
        return history
    total_chars = sum(len(m.get("content", "")) for m in history)
    while len(history) > 2 and total_chars > MAX_CONTEXT_CHARS:
        # Drop the oldest turn pair (user + assistant)
        dropped = history.pop(0)
        total_chars -= len(dropped.get("content", ""))
    return history


def _save_turn(db, conversation_id: str, company_id: str, user_id: str,
               user_message: str, assistant_message: str) -> None:
    """Persist a turn to the existing conversations.messages JSONB schema.

    Restrict updates to the owning user and tenant. Never overwrite a row
    belonging to another user, even when a conversation UUID is supplied.
    """
    try:
        query = (
            db.table("conversations").select("messages")
            .eq("id", conversation_id).eq("company_id", company_id)
            .eq("user_id", user_id).limit(1).execute()
        )
        rows = query.data or []
        new_turn = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ]
        if rows:
            existing = rows[0].get("messages") or []
            if not isinstance(existing, list):
                raise ValueError("Conversation messages are malformed")
            (
                db.table("conversations").update({"messages": existing + new_turn, "updated_at": datetime.now(timezone.utc).isoformat()})
                .eq("id", conversation_id).eq("company_id", company_id)
                .eq("user_id", user_id).execute()
            )
        else:
            # The caller must have generated a new conversation ID; never
            # create a new row for an unknown client-supplied ID.
            db.table("conversations").insert({
                "id": conversation_id, "company_id": company_id,
                "user_id": user_id, "messages": new_turn,
            }).execute()
    except Exception:
        log.exception("Failed to save conversation turn")
        raise


async def _fetch_data_for_intent(intent: str, message: str, adapter) -> dict:
    """
    Fetch real QB data matching the classified intent.
    Returns a dict consumed by build_query_context().
    """
    data: dict = {}

    try:
        if intent == "customs_status":
            data["customs"] = await adapter.get_customs_status()

        elif intent == "distributor_orders":
            data["distributor_orders"] = await adapter.get_ordering_system_data(message)

        elif intent == "inventory_check":
            items = await adapter.get_inventory_merged()
            # Convert to the format expected by summarize_inventory
            data["inventory"] = [
                {
                    "product_name": i.get("product_name", ""),
                    "total_qty": i.get("total_qty", 0),
                    "stock_status": _qty_to_status(i),
                    "weeks_remaining": _weeks_remaining(i),
                }
                for i in items
            ]

        elif intent == "account_status":
            customers = await adapter.get_all_customers()
            invoices = await adapter.get_orders_last_n_days(90)
            # Try to find a specific account named in the message
            msg_lower = message.lower()
            specific = next(
                (c for c in customers if getattr(c, "name", "").lower() in msg_lower), None
            )
            if specific:
                cid = getattr(specific, "qb_id", "")
                acct_invoices = [
                    inv for inv in invoices
                    if (getattr(inv, "customer_id", None) or inv.get("customer_id", "")) == cid
                ]
                data["account"] = {
                    "name": getattr(specific, "name", ""),
                    "health_status": "unknown",
                    "last_order_date": max(
                        (str(getattr(inv, "date", "") or inv.get("order_date", "")) for inv in acct_invoices),
                        default="never",
                    ),
                    "current_balance": float(getattr(specific, "balance", 0)),
                }
                data["orders"] = [
                    {
                        "order_date": str(getattr(inv, "date", "") or inv.get("order_date", "")),
                        "total_amount": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
                    }
                    for inv in acct_invoices
                ]
            else:
                # Overview of all accounts
                from app.intelligence.account_health import score_account
                data["accounts"] = []
                for customer in customers:
                    cid = getattr(customer, "qb_id", "")
                    cust_invoices = [
                        {"date": str(getattr(inv, "date", "")), "total": float(getattr(inv, "total", 0))}
                        for inv in invoices
                        if (getattr(inv, "customer_id", None) or inv.get("customer_id", "")) == cid
                    ]
                    dates = [o["date"] for o in cust_invoices if o["date"]]
                    last_order = None
                    if dates:
                        try:
                            last_order = date.fromisoformat(max(dates))
                        except ValueError:
                            pass
                    try:
                        balance = getattr(customer, "balance", None)
                        if balance is None or isinstance(balance, bool):
                            raise ValueError("Customer balance not observed")
                        health = score_account(
                            account_id=cid,
                            account_name=getattr(customer, "name", ""),
                            last_order_date=last_order,
                            avg_order_cycle_days=None,
                            order_history=cust_invoices,
                            current_balance=Decimal(str(balance)),
                        )
                    except (ValueError, TypeError, ArithmeticError):
                        # Credit balances and missing/malformed source values
                        # are unscorable, not zero or a reason to hide other
                        # customers from the chat overview.
                        data["accounts"].append({
                            "name": getattr(customer, "name", ""),
                            "health_status": "unknown",
                            "health_score": None,
                        })
                        continue
                    data["accounts"].append({
                        "name": getattr(customer, "name", ""),
                        "health_status": health.status,
                        "health_score": health.score,
                    })

        elif intent in ("sales_report", "general_question"):
            customers = await adapter.get_all_customers()
            invoices = await adapter.get_orders_last_n_days(30)
            items = await adapter.get_inventory_merged()

            from app.intelligence.account_health import score_account
            from app.intelligence.sales_analytics import compute_period_sales

            account_summaries = []
            for customer in customers[:30]:  # cap for context size
                account_summaries.append({
                    "name": getattr(customer, "name", ""),
                    "health_status": "unknown",  # quick pass, no scoring
                })

            invoice_dicts = [
                {
                    "customer_name": getattr(inv, "customer_name", "") or inv.get("customer_name", ""),
                    "order_date": str(getattr(inv, "date", "") or inv.get("order_date", "")),
                    "total_amount": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
                    "items": getattr(inv, "line_items", []) or inv.get("line_items", []),
                }
                for inv in invoices
            ]

            today = date.today()
            period = compute_period_sales(invoice_dicts, today - timedelta(days=30), today)
            data["accounts"] = account_summaries
            data["inventory"] = [
                {
                    "product_name": i.get("product_name", ""),
                    "total_qty": i.get("total_qty", 0),
                    "stock_status": _qty_to_status(i),
                    "weeks_remaining": _weeks_remaining(i),
                }
                for i in items
            ]
            data["sales_summary"] = {
                "total_revenue": float(period.total_revenue),
                "order_count": period.order_count,
                "top_accounts": period.top_accounts[:5],
            }

    except Exception as exc:
        log.warning("Data fetch for intent '%s' failed: %s", intent, exc)

    return data


def _qty_to_status(item: dict) -> str:
    """Map weeks_of_stock to a status string."""
    wks = _weeks_remaining(item)
    if wks is None:
        return "healthy"
    if wks <= 2:
        return "critical"
    if wks <= 4:
        return "low"
    return "healthy"


def _weeks_remaining(item: dict):
    sell_rate = item.get("weekly_sell_rate", 0)
    if not sell_rate:
        return None
    qty = item.get("total_qty", 0)
    return round(qty / sell_rate, 1) if sell_rate else None


def _detect_account_names(message: str, history: list[dict]) -> list[str]:
    """Heuristically extract account/customer names from message and recent history."""
    # Simple: look for capitalized multi-word sequences that aren't common words
    import re
    text = message + " " + " ".join(m.get("content", "")[:100] for m in history[-4:])
    # Match sequences like "Sunrise Distributors" or "Delta Corp"
    candidates = re.findall(r'\b([A-Z][a-z]+ (?:[A-Z][a-z]+ )?(?:Distributors?|Corp|Inc|LLC|Co\.|Company|Group|Trading|Imports?|Exports?|Supply|Foods?|Pharma|Industries?|International|Global|National))\b', text)
    return list(set(candidates))[:5]


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/message")
async def send_message(
    request: ChatRequest,
    user: dict = Depends(require_permission("view_own_accounts")),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """
    Send a message to SecretaryAI and get a streaming SSE response.
    Conversation history is loaded from and saved to Supabase.
    """
    company_id = user["company_id"]
    user_id = user.get("sub", user.get("user_id", ""))
    conversation_id = request.conversation_id or str(uuid.uuid4())
    if request.conversation_id:
        owned = (db.table('conversations').select('id')
                 .eq('id', conversation_id).eq('company_id', company_id)
                 .eq('user_id', user_id).limit(1).execute())
        if not owned.data:
            raise HTTPException(status_code=404, detail='Conversation not found')

    # 1. Load + trim conversation history
    history = _load_history(db, conversation_id, company_id, user_id)
    history = _trim_history(history)

    # 2. Classify intent
    intent = await classify_intent(request.message)
    log.debug("Intent classified: %s for message: %.60s", intent, request.message)

    # 3. Authorize the data scope BEFORE retrieving records. Sales reps have
    # view_inventory but no verified account assignment or financial scope.
    # A general-question intent previously loaded all company invoices and
    # customers; never let it do that for a rep.
    role = user.get("role", "viewer")
    if role == "sales_rep" and intent not in {"inventory_check", "general_question"}:
        raise HTTPException(status_code=403, detail="This business data requires a higher permission")
    if role == "sales_rep" and intent == "general_question":
        data = {}
    else:
        data = await _fetch_data_for_intent(intent, request.message, adapter)
    data_summary = build_query_context(intent, data)
    if role == "sales_rep" and intent == "general_question":
        data_summary = (
            "No business data was retrieved for this question. "
            "Do not infer or invent company-wide sales, customer, account, or financial records. "
            "The user's verified data scope is inventory only."
        )

    # 4. Build company context for system prompt
    company_context = {
        "company_name": user.get("company_name", "your company"),
        "business_type": user.get("business_type", "importer/distributor"),
        "user_role": user.get("role", "viewer"),
        "timezone": user.get("timezone", "America/New_York"),
        "preferred_language": user.get("preferred_language", "English"),
    }

    # Load memory context
    # Sales reps must not inherit historical company-wide summaries or
    # account memory that predates the permission hardening.
    recent_summaries = [] if role == "sales_rep" else load_recent_summaries(db, company_id, user_id)
    account_names = [] if role == "sales_rep" else _detect_account_names(request.message, history)
    account_mem = [] if role == "sales_rep" else load_account_memory(db, company_id, account_names)

    if recent_summaries or account_mem:
        memory_context = ""
        if recent_summaries:
            memory_context += f"\n\n[Recent conversation context]\n{recent_summaries}"
        if account_mem:
            memory_context += f"\n\n[Account memory]\n{account_mem}"
        data_summary = data_summary + memory_context

    # 5. Collect the full response text while streaming
    full_response: list[str] = []

    async def generate():
        async for chunk in stream_chat(
            request.message, history, data_summary, company_context
        ):
            full_response.append(chunk)
            yield f"data: {json.dumps({'text': chunk, 'conversation_id': conversation_id})}\n\n"

        # 6. Persist conversation turn
        assistant_text = "".join(full_response)
        try:
            _save_turn(db, conversation_id, company_id, user_id, request.message, assistant_text)
        except Exception:
            # A streamed response is not a completed conversation if the
            # database commit failed. Emit an explicit SSE error, not "done".
            yield f"data: {json.dumps({'error': 'Conversation could not be saved', 'conversation_id': conversation_id})}\n\n"
            return

        # Async background: summarise conversation + extract account memory
        import asyncio
        assistant_text_captured = "".join(full_response)
        try:
            if len(history) >= 6:  # summarise after a few turns
                asyncio.create_task(summarise_and_save(
                    db, company_id, user_id, conversation_id,
                    history + [{"role": "user", "content": request.message},
                               {"role": "assistant", "content": assistant_text_captured}]
                ))
            for name in account_names:
                if name:
                    extract_and_save_memory(db, company_id, name, assistant_text_captured)
        except Exception:
            pass

        # 7. Detect action proposals
        action = detect_action_in_response(assistant_text)
        if action:
            log.info("Action detected in chat response: %s", action.get("action_type"))
            yield f"data: {json.dumps({'action': action, 'conversation_id': conversation_id})}\n\n"

        yield f"data: {json.dumps({'done': True, 'conversation_id': conversation_id})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.get("/history/{conversation_id}")
async def get_conversation_history(
    conversation_id: str,
    limit: int = Query(40, ge=1, le=200),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Return the message history for a conversation."""
    result = (
        db.table("conversations").select("messages")
        .eq("id", conversation_id)
        .eq("company_id", user["company_id"])
        .eq("user_id", user["sub"]).limit(1).execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = result.data[0].get("messages") or []
    return {"conversation_id": conversation_id, "messages": messages[-limit:]}



@router.get("/conversations")
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List recent conversations for the current user."""
    result = (
        db.table("conversations")
        .select("id, title, messages, created_at, updated_at")
        .eq("company_id", user["company_id"])
        .eq("user_id", user["sub"])
        .order("updated_at", desc=True).limit(limit).execute()
    )
    convos = []
    for row in result.data or []:
        messages = row.get("messages") or []
        first_user = next((m.get("content", "") for m in messages
                           if isinstance(m, dict) and m.get("role") == "user"), "")
        convos.append({
            "conversation_id": row["id"],
            "preview": (row.get("title") or first_user)[:80],
            "created_at": row.get("created_at"),
        })
    return {"conversations": convos}



# ─── Voice Transcription ───────────────────────────────────────────────────────

@router.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile,
    user: dict = Depends(get_current_user),
):
    """
    Transcribe an audio file to text using OpenAI Whisper.
    Accepts any audio format supported by Whisper (m4a, mp3, wav, webm, ogg).
    Returns: { "text": "transcribed text" }
    """
    from app.config import get_settings
    cfg = get_settings()
    if not cfg.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="Speech-to-text is not configured. Set OPENAI_API_KEY in your environment.",
        )

    try:
        import openai
        client = openai.AsyncOpenAI(api_key=cfg.openai_api_key)
        audio_bytes = await audio.read()
        # Whisper requires a file-like object with a name attribute for format detection
        import io
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = audio.filename or "audio.m4a"
        transcript = await client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="en",
        )
        return {"text": transcript.text}
    except Exception as exc:
        log.exception("Whisper transcription failed")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}")
