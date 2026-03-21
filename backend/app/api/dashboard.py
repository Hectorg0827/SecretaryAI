"""
Dashboard API — summary, emails, sales chart, and follow-up notes.
All endpoints require a valid JWT. No special permission needed beyond login.
"""
import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_adapter, get_db
from app.auth.rbac import get_current_user, require_permission, ROLE_PERMISSIONS
from app.intelligence.account_health import score_account
from app.intelligence.inventory_monitor import evaluate_inventory

log = logging.getLogger(__name__)
router = APIRouter()


# ── /summary ──────────────────────────────────────────────────────────────────

@router.get("/summary")
async def get_dashboard_summary(
    user: dict = Depends(get_current_user),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """
    Single endpoint for the top-level dashboard: account health, inventory
    alerts (with correct field names), unread email count, pending approvals,
    and 30-day sales total.
    """
    company_id = user["company_id"]

    # ── Account health distribution ────────────────────────────────────────
    health_counts: Counter = Counter()
    try:
        customers = await adapter.get_all_customers()
        invoices_yr = await adapter.get_orders_last_n_days(365)

        by_customer: dict[str, list] = {}
        for inv in invoices_yr:
            cid = getattr(inv, "customer_id", None) or inv.get("customer_id", "")
            by_customer.setdefault(cid, []).append({
                "date": getattr(inv, "date", None) or inv.get("order_date"),
                "total": float(getattr(inv, "total", 0) or inv.get("total_amount", 0)),
            })

        for customer in customers:
            cid = getattr(customer, "qb_id", "") or customer.get("qb_id", "")
            orders = by_customer.get(cid, [])
            dates = [o["date"] for o in orders if o["date"]]
            last_order = max(dates, default=None)
            if isinstance(last_order, str):
                try:
                    from datetime import date as _date
                    last_order = _date.fromisoformat(str(last_order)[:10])
                except ValueError:
                    last_order = None

            health = score_account(
                account_id=cid,
                account_name=getattr(customer, "name", ""),
                last_order_date=last_order,
                avg_order_cycle_days=None,
                order_history=orders,
                current_balance=float(getattr(customer, "balance", 0)),
            )
            health_counts[health.status] += 1
    except Exception as exc:
        log.error("Dashboard summary: health scoring failed: %s", exc)

    # ── Inventory alerts (fields match frontend InventoryAlert interface) ──
    inventory_alerts: list[dict] = []
    try:
        inventory = await adapter.get_inventory_merged()
        for item in inventory:
            status = evaluate_inventory(
                item_id=item.get("qb_id", ""),
                product_name=item.get("product_name", ""),
                warehouse_1_qty=int(item.get("warehouse_qty", 0)),
                warehouse_2_qty=int(item.get("qb_qty", 0)),
                weekly_sell_rate=Decimal(str(item.get("weekly_sell_rate", 0))),
            )
            if status.status in ("critical", "low", "out_of_stock"):
                inventory_alerts.append({
                    "item_id":        item.get("qb_id", ""),
                    "product_name":   item["product_name"],
                    "total_qty":      item.get("total_qty", 0),
                    "weeks_remaining": status.weeks_remaining,
                    "stock_status":   status.status,
                    "needs_po":       True,
                })
    except Exception as exc:
        log.error("Dashboard summary: inventory eval failed: %s", exc)

    # ── 30-day sales total ─────────────────────────────────────────────────
    sales_30d = 0.0
    try:
        invoices_30 = await adapter.get_orders_last_n_days(30)
        sales_30d = sum(
            float(getattr(inv, "total", 0) or inv.get("total_amount", 0))
            for inv in invoices_30
        )
    except Exception as exc:
        log.error("Dashboard summary: sales total failed: %s", exc)

    # ── Pending approvals count ────────────────────────────────────────────
    pending_count = 0
    try:
        result = (
            db.table("drafts")
            .select("id", count="exact")
            .eq("company_id", company_id)
            .eq("status", "pending")
            .execute()
        )
        pending_count = result.count or 0
    except Exception as exc:
        log.warning("Dashboard summary: pending count failed: %s", exc)

    # ── Unread email count ─────────────────────────────────────────────────
    unread_count = 0
    try:
        emails = await adapter.get_emails(query="is:unread", max_results=50)
        unread_count = len(emails)
    except Exception as exc:
        log.warning("Dashboard summary: unread count failed: %s", exc)

    # ── Role-based field filtering ──────────────────────────────────────────
    role = user.get("role", "viewer")
    perms = ROLE_PERMISSIONS.get(role, set())
    has_view_all = "view_all" in perms

    return {
        "company_id": company_id,
        "accounts": {
            "healthy": health_counts.get("healthy", 0),
            "slowing": health_counts.get("slowing", 0),
            "at_risk": health_counts.get("at_risk", 0),
            "dormant": health_counts.get("dormant", 0),
            "total":   sum(health_counts.values()),
        },
        # Only roles with inventory access see alerts
        "inventory_alerts": inventory_alerts if ("view_inventory" in perms or has_view_all) else [],
        # Financial figures visible to owner + manager only
        "sales_30d":       round(sales_30d, 2) if ("view_financials" in perms or has_view_all) else None,
        # Approval count visible to approvers only
        "pending_actions": pending_count if ("approve_actions" in perms or has_view_all) else None,
        # Email count visible to roles with trigger_actions only
        "unread_emails":   unread_count if ("trigger_actions" in perms or has_view_all) else None,
    }


# ── Email helpers ──────────────────────────────────────────────────────────────

def _parse_from(from_str: str) -> tuple[str, str]:
    """'Name <email@x.com>' → (name, email).  Bare email → (local-part, email)."""
    m = re.match(r'^(.*?)\s*<([^>]+)>$', from_str.strip())
    if m:
        return m.group(1).strip().strip('"'), m.group(2).strip()
    if "@" in from_str:
        return from_str.split("@")[0], from_str.strip()
    return from_str, ""


async def _ai_analyze_emails(emails: list[dict]) -> list[dict]:
    """
    Call Claude Haiku once to batch-analyse up to 10 emails.
    Returns [{priority, summary, action_needed}] in order.
    Falls back to heuristic if Claude is unavailable.
    """
    if not emails:
        return []

    def _heuristic(e: dict) -> dict:
        subj = e.get("subject", "").lower()
        snippet = e.get("snippet", "").lower()
        if any(w in subj + snippet for w in ("urgent", "overdue", "past due", "critical", "delay")):
            priority = "high"
        elif any(w in subj + snippet for w in ("invoice", "order", "payment", "po ", "quote")):
            priority = "medium"
        else:
            priority = "low"
        return {
            "priority": priority,
            "summary": e.get("snippet", "")[:120],
            "action_needed": "Review email",
        }

    try:
        import anthropic
        from app.config import get_settings
        from app.ai.model_router import model_for
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        email_text = "\n\n".join(
            f"EMAIL {i+1}:\nFrom: {e['from']}\nSubject: {e['subject']}\n"
            f"Body: {e.get('snippet','')[:300]}"
            for i, e in enumerate(emails[:10])
        )
        prompt = (
            "You analyse emails for a small importer/distributor business.\n"
            "For each email provide:\n"
            "- priority: high / medium / low\n"
            "- summary: 1-2 sentences\n"
            "- action_needed: what the user should do (or 'No action required')\n\n"
            f"{email_text}\n\n"
            "Reply ONLY with a JSON array — one object per email in order:\n"
            '[{"priority":"...","summary":"...","action_needed":"..."}]'
        )
        response = await client.messages.create(
            model=model_for("email_priority_batch"),
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if "```" in text:
            text = re.split(r"```(?:json)?", text)[1]
        return json.loads(text.strip())
    except Exception as exc:
        log.warning("Email AI analysis failed (%s) — using heuristics", exc)
        return [_heuristic(e) for e in emails]


# ── /emails ────────────────────────────────────────────────────────────────────

@router.get("/emails")
async def list_emails(
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(require_permission("trigger_actions")),
    adapter=Depends(get_adapter),
):
    query = "is:unread" if unread_only else "in:inbox"
    raw = await adapter.get_emails(query=query, max_results=limit)

    if not raw:
        return {"emails": []}

    analyses = await _ai_analyze_emails(raw)

    result = []
    for i, email in enumerate(raw):
        analysis = analyses[i] if i < len(analyses) else {
            "priority": "medium",
            "summary": email.get("snippet", "")[:120],
            "action_needed": "Review email",
        }
        name, email_addr = _parse_from(email.get("from", ""))
        labels = email.get("labels", [])

        result.append({
            "id":              email["id"],
            "thread_id":       email.get("thread_id", email["id"]),
            "from":            name or email_addr,
            "from_email":      email_addr,
            "subject":         email.get("subject", "(no subject)"),
            "snippet":         email.get("snippet", ""),
            "ai_summary":      analysis.get("summary", ""),
            "ai_priority":     analysis.get("priority", "medium"),
            "ai_action_needed": analysis.get("action_needed", ""),
            "received_at":     email.get("date", ""),
            "is_read":         "UNREAD" not in labels,
            "labels":          labels,
        })

    return {"emails": result}


# ── /emails/draft-reply ────────────────────────────────────────────────────────

class DraftReplyRequest(BaseModel):
    email_id: str


@router.post("/emails/draft-reply")
async def draft_email_reply(
    body: DraftReplyRequest,
    user: dict = Depends(require_permission("trigger_actions")),
    adapter=Depends(get_adapter),
):
    """Generate an AI reply draft for a given email."""
    # Fetch up to recent emails to find context; fall back to a generic prompt
    email_ctx: dict = {"from": "", "subject": "", "snippet": ""}
    try:
        recent = await adapter.get_emails(query=f"rfc822msgid:{body.email_id}", max_results=1)
        if not recent:
            recent = await adapter.get_emails(query="in:inbox", max_results=10)
            recent = [e for e in recent if e.get("id") == body.email_id]
        if recent:
            email_ctx = recent[0]
    except Exception:
        pass

    try:
        import anthropic
        from app.config import get_settings
        settings = get_settings()
        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        prompt = (
            "You are a professional assistant for a small importer/distributor business. "
            "Write a professional, concise email reply (3-5 sentences max).\n\n"
            f"Original email:\nFrom: {email_ctx.get('from','')}\n"
            f"Subject: {email_ctx.get('subject','')}\n"
            f"Body: {email_ctx.get('snippet','')}\n\n"
            "Write ONLY the reply body — no subject line, no 'Subject:' prefix. "
            "Start with a greeting. End with a professional sign-off."
        )
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        draft_text = response.content[0].text.strip()
    except Exception as exc:
        log.error("Draft reply generation failed: %s", exc)
        draft_text = (
            "Thank you for your email. I have reviewed your message and will "
            "follow up with you shortly.\n\nBest regards"
        )

    subj = email_ctx.get("subject", "")
    reply_subject = f"Re: {subj}" if subj and not subj.lower().startswith("re:") else subj

    return {"draft": draft_text, "subject": reply_subject}


# ── /emails/send-reply ─────────────────────────────────────────────────────────

class SendReplyRequest(BaseModel):
    email_id: str
    body: str
    subject: str
    to: str  # recipient — frontend must pass from_email of the original email


@router.post("/emails/send-reply")
async def send_email_reply(
    body: SendReplyRequest,
    user: dict = Depends(require_permission("trigger_actions")),
    adapter=Depends(get_adapter),
    db=Depends(get_db),
):
    """Send a reply email. The user has already reviewed and approved the draft."""
    company_id = user["company_id"]
    try:
        result = await adapter.send_email_reply(
            to=body.to, subject=body.subject, body=body.body
        )
        # Audit log
        try:
            db.table("action_log").insert({
                "company_id":    company_id,
                "actor":         user["sub"],
                "action_type":   "email_reply_sent",
                "autonomy_level": "DRAFT_AND_WAIT",
                "description":   f"Email reply sent to {body.to}: {body.subject}",
                "data_involved": {"email_id": body.email_id, "to": body.to},
                "status":        "completed",
            }).execute()
        except Exception:
            pass
        return {"status": "sent", "message_id": result.get("message_id")}
    except Exception as exc:
        log.error("Send email reply failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Failed to send email: {exc}")


# ── /emails/{id}/mark-read ─────────────────────────────────────────────────────

@router.post("/emails/{email_id}/mark-read")
async def mark_email_read(
    email_id: str,
    user: dict = Depends(require_permission("trigger_actions")),
    adapter=Depends(get_adapter),
):
    """Remove the UNREAD label from a Gmail message."""
    try:
        await adapter.mark_email_read(email_id)
    except Exception as exc:
        log.warning("mark-read failed for %s: %s", email_id, exc)
    return {"status": "ok"}


# ── /sales ─────────────────────────────────────────────────────────────────────

@router.get("/sales")
async def get_sales_data(
    days: int = Query(30, ge=7, le=365),
    user: dict = Depends(require_permission("view_financials")),
    adapter=Depends(get_adapter),
):
    """Sales summary + per-day chart data for the requested period."""
    today = date.today()

    try:
        invoices = await adapter.get_orders_last_n_days(days)
    except Exception as exc:
        log.error("Sales data fetch failed: %s", exc)
        invoices = []

    daily: dict[str, dict] = defaultdict(lambda: {"revenue": 0.0, "order_count": 0})
    account_revenue: dict[str, float] = defaultdict(float)
    total_revenue = 0.0
    order_count = 0

    for inv in invoices:
        inv_date = getattr(inv, "date", None) or inv.get("order_date")
        inv_total = float(getattr(inv, "total", 0) or inv.get("total_amount", 0))
        customer_name = getattr(inv, "customer_name", "") or inv.get("customer_name", "")

        if inv_date:
            d = str(inv_date)[:10]
            daily[d]["revenue"] += inv_total
            daily[d]["order_count"] += 1

        total_revenue += inv_total
        order_count += 1
        if customer_name:
            account_revenue[customer_name] += inv_total

    # Build chart data — fill every calendar day (empty days = 0)
    chart_data = []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        chart_data.append({
            "date":        d,
            "revenue":     round(daily.get(d, {}).get("revenue", 0.0), 2),
            "order_count": daily.get(d, {}).get("order_count", 0),
        })

    # Prior-period comparison
    vs_prior_pct = 0.0
    try:
        cutoff_str = (today - timedelta(days=days)).isoformat()
        prior_invoices = await adapter.get_orders_last_n_days(days * 2)
        prior_total = 0.0
        for inv in prior_invoices:
            d = str(getattr(inv, "date", None) or inv.get("order_date", "") or "")[:10]
            if d and d < cutoff_str:
                prior_total += float(getattr(inv, "total", 0) or inv.get("total_amount", 0))
        if prior_total > 0:
            vs_prior_pct = round((total_revenue - prior_total) / prior_total * 100, 1)
    except Exception:
        pass

    top_account = (
        max(account_revenue, key=lambda k: account_revenue[k])
        if account_revenue else ""
    )
    avg_order_value = round(total_revenue / order_count, 2) if order_count > 0 else 0.0

    return {
        "total_revenue":      round(total_revenue, 2),
        "order_count":        order_count,
        "avg_order_value":    avg_order_value,
        "top_account":        top_account,
        "vs_prior_period_pct": vs_prior_pct,
        "chart_data":         chart_data,
    }


# ── /notes ─────────────────────────────────────────────────────────────────────

@router.get("/notes")
async def list_notes(
    user: dict = Depends(require_permission("view_own_accounts")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    try:
        result = (
            db.table("follow_up_notes")
            .select("*")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .execute()
        )
        return {"notes": result.data or []}
    except Exception as exc:
        log.error("Notes fetch failed: %s", exc)
        return {"notes": []}


class CreateNoteRequest(BaseModel):
    text: str
    account_name: Optional[str] = None
    due_date: Optional[str] = None


@router.post("/notes")
async def create_note(
    body: CreateNoteRequest,
    user: dict = Depends(require_permission("view_own_accounts")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    record = {
        "id":           str(uuid.uuid4()),
        "company_id":   company_id,
        "user_id":      user["sub"],
        "text":         body.text,
        "account_name": body.account_name,
        "due_date":     body.due_date,
        "done":         False,
    }
    try:
        result = db.table("follow_up_notes").insert(record).execute()
        return result.data[0] if result.data else record
    except Exception as exc:
        log.error("Note creation failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create note")


class UpdateNoteRequest(BaseModel):
    done: Optional[bool] = None
    text: Optional[str] = None


@router.patch("/notes/{note_id}")
async def update_note(
    note_id: str,
    body: UpdateNoteRequest,
    user: dict = Depends(require_permission("view_own_accounts")),
    db=Depends(get_db),
):
    company_id = user["company_id"]
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        result = (
            db.table("follow_up_notes")
            .update(update)
            .eq("id", note_id)
            .eq("company_id", company_id)
            .execute()
        )
        return result.data[0] if result.data else {"id": note_id, **update}
    except Exception as exc:
        log.error("Note update failed: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to update note")
