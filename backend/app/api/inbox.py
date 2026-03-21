"""
Unified Inbox API
─────────────────
Aggregates emails, inventory alerts, pending approvals, and account health
events into a single prioritised feed — the "single pane of glass" data layer.

GET  /api/inbox              → unified feed (sorted: unread → priority → type)
PATCH /api/inbox/{id}/read   → mark an item as read
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.dependencies import get_current_user
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/inbox", tags=["inbox"])

# ── Types ─────────────────────────────────────────────────────────────────────

ItemType = Literal["email", "alert", "approval", "health_event"]
Priority = Literal["high", "medium", "low"]

_PRIORITY_WEIGHT = {"high": 0, "medium": 1, "low": 2}
_TYPE_WEIGHT     = {"approval": 0, "alert": 1, "email": 2, "health_event": 3}


class InboxItem(BaseModel):
    id:       str
    type:     ItemType
    priority: Priority
    title:    str
    subtitle: str
    timestamp: str
    is_read:  bool
    payload:  dict


class InboxFeed(BaseModel):
    items:        list[InboxItem]
    unread_count: int
    total_count:  int


def _sort_key(item: InboxItem) -> tuple:
    return (
        0 if not item.is_read else 1,
        _PRIORITY_WEIGHT[item.priority],
        _TYPE_WEIGHT[item.type],
    )


# ── GET /api/inbox ─────────────────────────────────────────────────────────────

@router.get("", response_model=InboxFeed)
async def get_inbox_feed(
    filter_type: Optional[str] = Query(None, alias="filter"),
    limit: int = Query(50, le=200),
    user: dict = Depends(get_current_user),
):
    """Return unified inbox sorted by urgency."""
    company_id: str = user["company_id"]
    role: str = user.get("role", "viewer")

    from supabase import create_client
    settings = get_settings()
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    items: list[InboxItem] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Pending approvals (owner / manager) ─────────────────────────────
    if role in ("owner", "manager"):
        try:
            res = (
                db.table("drafts")
                .select("id, action_type, content, created_at")
                .eq("company_id", company_id)
                .eq("status", "pending")
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
            for d in res.data or []:
                atype   = d.get("action_type", "")
                content = d.get("content") or {}
                if "email" in atype:
                    title    = f"Email draft — {content.get('to', 'unknown recipient')}"
                    subtitle = content.get("subject", "No subject")
                elif "purchase_order" in atype:
                    vendor   = content.get("vendor_name", "Unknown vendor")
                    total    = content.get("total_amount", 0)
                    title    = f"PO draft — {vendor}"
                    subtitle = f"${total:,.2f} awaiting approval" if total else "Review required"
                else:
                    title    = atype.replace("_", " ").title()
                    subtitle = "Awaiting your approval"

                items.append(InboxItem(
                    id=f"approval:{d['id']}",
                    type="approval",
                    priority="high",
                    title=title,
                    subtitle=subtitle,
                    timestamp=d["created_at"],
                    is_read=False,
                    payload={"draft_id": d["id"], "action_type": atype, "content": content},
                ))
        except Exception as exc:
            log.warning("inbox: approvals load failed: %s", exc)

    # ── 2. Inventory alerts ────────────────────────────────────────────────
    if role in ("owner", "manager", "back_office", "sales_rep"):
        try:
            res = (
                db.table("inventory")
                .select("item_id, product_name, stock_status, qty_on_hand, weeks_remaining")
                .eq("company_id", company_id)
                .in_("stock_status", ["out_of_stock", "critical", "low"])
                .order("weeks_remaining", desc=False)
                .limit(15)
                .execute()
            )
            for item in res.data or []:
                status = item.get("stock_status", "low")
                weeks  = item.get("weeks_remaining")
                priority: Priority = (
                    "high"   if status == "out_of_stock" else
                    "medium" if status == "critical"     else
                    "low"
                )
                weeks_txt = f"{weeks:.1f}w remaining" if weeks is not None else "qty unknown"
                items.append(InboxItem(
                    id=f"alert:inv:{item['item_id']}",
                    type="alert",
                    priority=priority,
                    title=item.get("product_name", "Unknown product"),
                    subtitle=f"{status.replace('_', ' ').title()} — {weeks_txt}",
                    timestamp=now_iso,
                    is_read=(status == "low"),
                    payload={
                        "item_id":       item["item_id"],
                        "product_name":  item.get("product_name"),
                        "stock_status":  status,
                        "qty_on_hand":   item.get("qty_on_hand"),
                        "weeks_remaining": weeks,
                    },
                ))
        except Exception as exc:
            log.warning("inbox: inventory load failed: %s", exc)

    # ── 3. Priority emails (high + medium only) ────────────────────────────
    if role in ("owner", "manager"):
        try:
            # Pull cached email analysis from the companies/emails table
            res = (
                db.table("emails")
                .select("id, thread_id, from_name, from_email, subject, ai_priority, ai_summary, ai_action_needed, received_at, is_read")
                .eq("company_id", company_id)
                .in_("ai_priority", ["high", "medium"])
                .order("received_at", desc=True)
                .limit(20)
                .execute()
            )
            for email in res.data or []:
                ai_priority = email.get("ai_priority", "medium")
                items.append(InboxItem(
                    id=f"email:{email.get('id', email.get('thread_id', ''))}",
                    type="email",
                    priority=ai_priority,
                    title=email.get("from_name") or email.get("from_email", "Unknown sender"),
                    subtitle=email.get("subject", "(no subject)"),
                    timestamp=email.get("received_at", now_iso),
                    is_read=email.get("is_read", False),
                    payload=email,
                ))
        except Exception as exc:
            # emails table may not exist yet — fall back gracefully
            log.debug("inbox: emails load failed (table may not exist): %s", exc)

    # ── 4. Account health events (at_risk / dormant) ───────────────────────
    if role in ("owner", "manager"):
        try:
            res = (
                db.table("accounts")
                .select("id, name, health_status, health_score, last_order_date")
                .eq("company_id", company_id)
                .in_("health_status", ["at_risk", "dormant"])
                .order("health_score")
                .limit(10)
                .execute()
            )
            for acc in res.data or []:
                status     = acc.get("health_status", "at_risk")
                last_order = (acc.get("last_order_date") or "")[:10]
                subtitle   = f"Last order: {last_order}" if last_order else "No recent orders"
                items.append(InboxItem(
                    id=f"health:{acc['id']}",
                    type="health_event",
                    priority="high" if status == "dormant" else "medium",
                    title=acc.get("name", "Unknown account"),
                    subtitle=f"{status.replace('_', ' ').title()} — {subtitle}",
                    timestamp=last_order or now_iso,
                    is_read=False,
                    payload={
                        "account_id":     acc["id"],
                        "account_name":   acc.get("name"),
                        "health_status":  status,
                        "health_score":   acc.get("health_score"),
                        "last_order_date": acc.get("last_order_date"),
                    },
                ))
        except Exception as exc:
            log.warning("inbox: health events load failed: %s", exc)

    # ── Filter + sort + paginate ───────────────────────────────────────────
    if filter_type and filter_type != "all":
        items = [i for i in items if i.type == filter_type]

    items.sort(key=_sort_key)
    items = items[:limit]

    return InboxFeed(
        items=items,
        unread_count=sum(1 for i in items if not i.is_read),
        total_count=len(items),
    )


# ── PATCH /api/inbox/{id}/read ─────────────────────────────────────────────────

@router.patch("/{item_id}/read")
async def mark_inbox_item_read(
    item_id: str,
    user: dict = Depends(get_current_user),
):
    """Mark an inbox item as read. Handles email, approval, and health_event types."""
    company_id: str = user["company_id"]

    if not item_id or ":" not in item_id:
        return {"ok": True}

    item_type = item_id.split(":")[0]

    from supabase import create_client
    settings = get_settings()
    db = create_client(settings.supabase_url, settings.supabase_service_role_key)

    try:
        if item_type == "email":
            email_id = item_id.split(":", 1)[1]
            db.table("emails").update({"is_read": True}).eq("id", email_id).eq("company_id", company_id).execute()
        elif item_type == "health":
            # Mark health events as acknowledged via a flag on accounts
            account_id = item_id.split(":", 1)[1]
            db.table("accounts").update({"health_event_acknowledged": True}).eq("id", account_id).eq("company_id", company_id).execute()
        # alerts and approvals don't have a "read" state to persist — they clear when resolved
    except Exception as exc:
        log.warning("inbox: mark_read failed for %s: %s", item_id, exc)

    return {"ok": True}
