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
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.api.deps import get_adapter, get_db
from app.auth.rbac import get_current_user, require_permission
from app.ai.secretary import classify_intent, stream_chat, chat as ai_chat, detect_action_in_response
from app.ai.data_summarizer import build_query_context

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


class ChatMessage(BaseModel):
    role: str
    content: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_history(db, conversation_id: str) -> list[dict]:
    """Load conversation history from Supabase, most recent turns first."""
    result = (
        db.table("conversations")
        .select("role, content")
        .eq("conversation_id", conversation_id)
        .order("created_at", desc=False)
        .limit(MAX_HISTORY_TURNS * 2)  # each turn = 2 rows (user + assistant)
        .execute()
    )
    return result.data or []


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
    """Persist a conversation turn (user + assistant) to Supabase."""
    rows = [
        {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "company_id": company_id,
            "user_id": user_id,
            "role": "user",
            "content": user_message,
        },
        {
            "id": str(uuid.uuid4()),
            "conversation_id": conversation_id,
            "company_id": company_id,
            "user_id": user_id,
            "role": "assistant",
            "content": assistant_message,
        },
    ]
    try:
        db.table("conversations").insert(rows).execute()
    except Exception as exc:
        log.error("Failed to save conversation turn: %s", exc)


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
                    health = score_account(
                        account_id=cid,
                        account_name=getattr(customer, "name", ""),
                        last_order_date=last_order,
                        avg_order_cycle_days=None,
                        order_history=cust_invoices,
                        current_balance=float(getattr(customer, "balance", 0)),
                    )
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

    # 1. Load + trim conversation history
    history = _load_history(db, conversation_id)
    history = _trim_history(history)

    # 2. Classify intent
    intent = await classify_intent(request.message)
    log.debug("Intent classified: %s for message: %.60s", intent, request.message)

    # 3. Fetch real data for the intent
    data = await _fetch_data_for_intent(intent, request.message, adapter)
    data_summary = build_query_context(intent, data)

    # 4. Build company context for system prompt
    company_context = {
        "company_name": user.get("company_name", "your company"),
        "business_type": user.get("business_type", "importer/distributor"),
        "user_role": user.get("role", "viewer"),
        "timezone": user.get("timezone", "America/New_York"),
        "preferred_language": user.get("preferred_language", "English"),
    }

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
        _save_turn(db, conversation_id, company_id, user_id, request.message, assistant_text)

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
        db.table("conversations")
        .select("id, role, content, created_at")
        .eq("conversation_id", conversation_id)
        .eq("company_id", user["company_id"])
        .eq("user_id", user["sub"])        # prevent cross-user history access
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    return {"conversation_id": conversation_id, "messages": result.data or []}


@router.get("/conversations")
async def list_conversations(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List recent conversations for the current user."""
    result = (
        db.table("conversations")
        .select("conversation_id, content, created_at")
        .eq("company_id", user["company_id"])
        .eq("user_id", user.get("sub", ""))
        .eq("role", "user")  # only user turns = one row per conversation start
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    # Deduplicate by conversation_id, keep most recent
    seen: set[str] = set()
    convos = []
    for row in (result.data or []):
        cid = row["conversation_id"]
        if cid not in seen:
            seen.add(cid)
            convos.append({
                "conversation_id": cid,
                "preview": row["content"][:80],
                "created_at": row["created_at"],
            })
    return {"conversations": convos}
