"""Billing and subscription management API."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.config import get_settings

log = logging.getLogger(__name__)
router = APIRouter()

settings = get_settings()

# ── Stripe setup ───────────────────────────────────────────────────────────────
try:
    import stripe as _stripe
    _stripe.api_key = settings.stripe_secret_key
    _stripe_available = bool(settings.stripe_secret_key)
except ImportError:
    _stripe = None  # type: ignore
    _stripe_available = False
    if settings.stripe_secret_key:
        # Loud, not silent: an operator who configured Stripe must not discover
        # months later that checkout was returning mock URLs.
        log.error(
            "STRIPE_SECRET_KEY is set but the 'stripe' package is not installed — "
            "billing is running in MOCK mode (no real checkout, webhooks ignored)"
        )

# ── Request/Response models ────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str
    billing_period: str  # 'monthly' | 'yearly'


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_create_stripe_customer(company_id: str, company_email: str, db) -> Optional[str]:
    """Return existing stripe_customer_id or create a new Stripe customer."""
    if not _stripe_available:
        return None

    result = db.table("company_subscriptions").select("stripe_customer_id").eq("company_id", company_id).execute()
    if result.data and result.data[0].get("stripe_customer_id"):
        return result.data[0]["stripe_customer_id"]

    customer = _stripe.Customer.create(
        email=company_email,
        metadata={"company_id": company_id},
    )
    return customer["id"]


def _resolve_price_id(plan: dict, billing_period: str) -> Optional[str]:
    """Return the Stripe price ID for the given plan and billing period."""
    if billing_period == "yearly":
        return plan.get("stripe_price_id_yearly")
    return plan.get("stripe_price_id_monthly")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans(db=Depends(get_db)):
    """List all subscription plans. Public — no auth required."""
    result = db.table("subscription_plans").select("*").execute()
    return {"plans": result.data or []}


@router.get("/subscription")
async def get_subscription(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get the current company subscription along with full plan details."""
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="User has no associated company")

    sub_result = (
        db.table("company_subscriptions")
        .select("*")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not sub_result.data:
        return {"subscription": None, "plan": None}

    subscription = sub_result.data[0]
    plan_result = (
        db.table("subscription_plans")
        .select("*")
        .eq("id", subscription["plan_id"])
        .execute()
    )
    plan = plan_result.data[0] if plan_result.data else None

    return {"subscription": subscription, "plan": plan}


@router.post("/checkout")
async def create_checkout_session(
    body: CheckoutRequest,
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Create a Stripe Checkout session for the requested plan.
    Returns checkout_url and session_id.
    Falls back to a mock response when Stripe is not configured.
    """
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="User has no associated company")

    if body.billing_period not in ("monthly", "yearly"):
        raise HTTPException(status_code=422, detail="billing_period must be 'monthly' or 'yearly'")

    # Fetch the plan
    plan_result = db.table("subscription_plans").select("*").eq("id", body.plan_id).execute()
    if not plan_result.data:
        raise HTTPException(status_code=404, detail=f"Plan '{body.plan_id}' not found")
    plan = plan_result.data[0]

    # Mock mode — Stripe not configured
    if not _stripe_available:
        log.warning("Stripe not configured — returning mock checkout URL")
        return {
            "checkout_url": f"https://mock-stripe.example.com/checkout?plan={body.plan_id}&period={body.billing_period}",
            "session_id": "mock_session_id",
        }

    price_id = _resolve_price_id(plan, body.billing_period)
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail=f"No Stripe price configured for plan '{body.plan_id}' ({body.billing_period})",
        )

    # Fetch company email for customer lookup/creation
    company_result = db.table("companies").select("email,name").eq("id", company_id).execute()
    company_email = ""
    if company_result.data:
        company_email = company_result.data[0].get("email", "")

    frontend_url = settings.frontend_url or "http://localhost:5173"

    try:
        customer_id = _get_or_create_stripe_customer(company_id, company_email, db)

        session_kwargs: dict = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{frontend_url}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{frontend_url}/billing/cancel",
            "metadata": {
                "company_id": company_id,
                "plan_id": body.plan_id,
                "billing_period": body.billing_period,
            },
        }
        if customer_id:
            session_kwargs["customer"] = customer_id

        session = _stripe.checkout.Session.create(**session_kwargs)
    except _stripe.StripeError as exc:
        log.error("Stripe checkout session creation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message or str(exc)}")

    return {"checkout_url": session.url, "session_id": session.id}


@router.post("/portal")
async def create_portal_session(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """
    Create a Stripe Customer Portal session so users can manage their subscription.
    Returns portal_url.
    """
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="User has no associated company")

    if not _stripe_available:
        log.warning("Stripe not configured — returning mock portal URL")
        return {"portal_url": "https://mock-stripe.example.com/portal"}

    sub_result = (
        db.table("company_subscriptions")
        .select("stripe_customer_id")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not sub_result.data or not sub_result.data[0].get("stripe_customer_id"):
        raise HTTPException(status_code=404, detail="No Stripe customer found for this company")

    stripe_customer_id = sub_result.data[0]["stripe_customer_id"]
    frontend_url = settings.frontend_url or "http://localhost:5173"

    try:
        portal_session = _stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=f"{frontend_url}/billing",
        )
    except _stripe.StripeError as exc:
        log.error("Stripe portal session creation failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Stripe error: {exc.user_message or str(exc)}")

    return {"portal_url": portal_session.url}


@router.get("/invoices")
async def list_invoices(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List billing event history for the current company."""
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="User has no associated company")

    result = (
        db.table("billing_events")
        .select("*")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )
    return {"invoices": result.data or []}


@router.post("/webhook")
async def stripe_webhook(request: Request, db=Depends(get_db)):
    """
    Stripe webhook handler.
    Verifies signature and processes subscription lifecycle events.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if not _stripe_available:
        log.warning("Stripe not configured — ignoring webhook")
        return {"received": True}

    # Verify webhook signature
    try:
        event = _stripe.Webhook.construct_event(
            payload, sig_header, settings.stripe_webhook_secret
        )
    except ValueError:
        log.warning("Stripe webhook: invalid payload")
        raise HTTPException(status_code=400, detail="Invalid payload")
    except _stripe.SignatureVerificationError:
        log.warning("Stripe webhook: invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    event_data = event["data"]["object"]
    stripe_event_id = event["id"]

    log.info("Stripe webhook received: %s (id=%s)", event_type, stripe_event_id)

    try:
        if event_type == "checkout.session.completed":
            await _handle_checkout_completed(event_data, stripe_event_id, db)

        elif event_type == "invoice.paid":
            await _handle_invoice_paid(event_data, stripe_event_id, db)

        elif event_type == "invoice.payment_failed":
            await _handle_invoice_payment_failed(event_data, stripe_event_id, db)

        elif event_type == "customer.subscription.updated":
            await _handle_subscription_updated(event_data, stripe_event_id, db)

        elif event_type == "customer.subscription.deleted":
            await _handle_subscription_deleted(event_data, stripe_event_id, db)

        else:
            log.debug("Stripe webhook: unhandled event type %s", event_type)

    except Exception as exc:
        log.error("Stripe webhook handler error for %s: %s", event_type, exc, exc_info=True)
        # Return 200 to Stripe so it does not retry; log internally
        return {"received": True, "warning": "handler error — logged internally"}

    return {"received": True}


# ── Webhook event handlers ─────────────────────────────────────────────────────

async def _handle_checkout_completed(session: dict, stripe_event_id: str, db) -> None:
    """Activate or create a subscription after a successful checkout."""
    metadata = session.get("metadata") or {}
    company_id = metadata.get("company_id")
    plan_id = metadata.get("plan_id")
    stripe_subscription_id = session.get("subscription")
    stripe_customer_id = session.get("customer")

    if not company_id or not plan_id:
        log.warning("checkout.session.completed missing company_id or plan_id in metadata")
        return

    # Retrieve full subscription from Stripe to get period dates
    period_start = None
    period_end = None
    if stripe_subscription_id and _stripe_available:
        try:
            stripe_sub = _stripe.Subscription.retrieve(stripe_subscription_id)
            period_start = datetime.fromtimestamp(stripe_sub["current_period_start"], tz=timezone.utc).isoformat()
            period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc).isoformat()
        except Exception as exc:
            log.warning("Could not retrieve Stripe subscription %s: %s", stripe_subscription_id, exc)

    now = datetime.now(tz=timezone.utc).isoformat()

    # Upsert company_subscriptions row
    existing = (
        db.table("company_subscriptions")
        .select("id")
        .eq("company_id", company_id)
        .execute()
    )
    sub_payload = {
        "company_id": company_id,
        "plan_id": plan_id,
        "status": "active",
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "cancel_at_period_end": False,
        "updated_at": now,
    }

    if existing.data:
        db.table("company_subscriptions").update(sub_payload).eq("company_id", company_id).execute()
    else:
        sub_payload["created_at"] = now
        db.table("company_subscriptions").insert(sub_payload).execute()

    _record_billing_event(
        db=db,
        company_id=company_id,
        event_type="checkout.session.completed",
        amount_usd=None,
        stripe_event_id=stripe_event_id,
        metadata={"plan_id": plan_id, "stripe_subscription_id": stripe_subscription_id},
    )


async def _handle_invoice_paid(invoice: dict, stripe_event_id: str, db) -> None:
    """Record a successful payment and refresh subscription period."""
    stripe_customer_id = invoice.get("customer")
    stripe_subscription_id = invoice.get("subscription")
    amount_paid = (invoice.get("amount_paid") or 0) / 100  # cents → dollars

    company_id = _company_id_from_customer(stripe_customer_id, db)
    if not company_id:
        log.warning("invoice.paid: no company found for customer %s", stripe_customer_id)
        return

    # Refresh period dates
    period_start = None
    period_end = None
    lines = invoice.get("lines", {}).get("data", [])
    if lines:
        period_start = datetime.fromtimestamp(lines[0]["period"]["start"], tz=timezone.utc).isoformat()
        period_end = datetime.fromtimestamp(lines[0]["period"]["end"], tz=timezone.utc).isoformat()

    now = datetime.now(tz=timezone.utc).isoformat()
    db.table("company_subscriptions").update({
        "status": "active",
        "current_period_start": period_start,
        "current_period_end": period_end,
        "updated_at": now,
    }).eq("company_id", company_id).execute()

    _record_billing_event(
        db=db,
        company_id=company_id,
        event_type="invoice.paid",
        amount_usd=amount_paid,
        stripe_event_id=stripe_event_id,
        metadata={
            "stripe_invoice_id": invoice.get("id"),
            "stripe_subscription_id": stripe_subscription_id,
        },
    )


async def _handle_invoice_payment_failed(invoice: dict, stripe_event_id: str, db) -> None:
    """Mark subscription as past_due on payment failure."""
    stripe_customer_id = invoice.get("customer")
    amount_due = (invoice.get("amount_due") or 0) / 100

    company_id = _company_id_from_customer(stripe_customer_id, db)
    if not company_id:
        log.warning("invoice.payment_failed: no company found for customer %s", stripe_customer_id)
        return

    now = datetime.now(tz=timezone.utc).isoformat()
    db.table("company_subscriptions").update({
        "status": "past_due",
        "updated_at": now,
    }).eq("company_id", company_id).execute()

    _record_billing_event(
        db=db,
        company_id=company_id,
        event_type="invoice.payment_failed",
        amount_usd=amount_due,
        stripe_event_id=stripe_event_id,
        metadata={"stripe_invoice_id": invoice.get("id")},
    )


async def _handle_subscription_updated(stripe_sub: dict, stripe_event_id: str, db) -> None:
    """Sync subscription status/plan changes from Stripe."""
    stripe_customer_id = stripe_sub.get("customer")
    stripe_subscription_id = stripe_sub.get("id")

    company_id = _company_id_from_customer(stripe_customer_id, db)
    if not company_id:
        log.warning("customer.subscription.updated: no company found for customer %s", stripe_customer_id)
        return

    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "canceled",
        "trialing": "trialing",
        "incomplete": "past_due",
        "incomplete_expired": "canceled",
        "unpaid": "past_due",
        "paused": "past_due",
    }
    new_status = status_map.get(stripe_sub.get("status", ""), "past_due")
    period_start = datetime.fromtimestamp(stripe_sub["current_period_start"], tz=timezone.utc).isoformat()
    period_end = datetime.fromtimestamp(stripe_sub["current_period_end"], tz=timezone.utc).isoformat()
    trial_end = None
    if stripe_sub.get("trial_end"):
        trial_end = datetime.fromtimestamp(stripe_sub["trial_end"], tz=timezone.utc).isoformat()

    cancel_at_period_end = stripe_sub.get("cancel_at_period_end", False)

    # Attempt to identify plan from price metadata
    plan_id = None
    items_data = stripe_sub.get("items", {}).get("data", [])
    if items_data:
        price_id = items_data[0].get("price", {}).get("id")
        if price_id:
            plan_monthly = db.table("subscription_plans").select("id").eq("stripe_price_id_monthly", price_id).execute()
            plan_yearly = db.table("subscription_plans").select("id").eq("stripe_price_id_yearly", price_id).execute()
            if plan_monthly.data:
                plan_id = plan_monthly.data[0]["id"]
            elif plan_yearly.data:
                plan_id = plan_yearly.data[0]["id"]

    now = datetime.now(tz=timezone.utc).isoformat()
    update_payload: dict = {
        "status": new_status,
        "stripe_subscription_id": stripe_subscription_id,
        "current_period_start": period_start,
        "current_period_end": period_end,
        "trial_end": trial_end,
        "cancel_at_period_end": cancel_at_period_end,
        "updated_at": now,
    }
    if plan_id:
        update_payload["plan_id"] = plan_id

    db.table("company_subscriptions").update(update_payload).eq("company_id", company_id).execute()

    _record_billing_event(
        db=db,
        company_id=company_id,
        event_type="customer.subscription.updated",
        amount_usd=None,
        stripe_event_id=stripe_event_id,
        metadata={"new_status": new_status, "cancel_at_period_end": cancel_at_period_end},
    )


async def _handle_subscription_deleted(stripe_sub: dict, stripe_event_id: str, db) -> None:
    """Mark subscription as canceled when Stripe deletes it."""
    stripe_customer_id = stripe_sub.get("customer")

    company_id = _company_id_from_customer(stripe_customer_id, db)
    if not company_id:
        log.warning("customer.subscription.deleted: no company found for customer %s", stripe_customer_id)
        return

    now = datetime.now(tz=timezone.utc).isoformat()
    db.table("company_subscriptions").update({
        "status": "canceled",
        "cancel_at_period_end": False,
        "updated_at": now,
    }).eq("company_id", company_id).execute()

    _record_billing_event(
        db=db,
        company_id=company_id,
        event_type="customer.subscription.deleted",
        amount_usd=None,
        stripe_event_id=stripe_event_id,
        metadata={},
    )


# ── Utility helpers ────────────────────────────────────────────────────────────

def _company_id_from_customer(stripe_customer_id: Optional[str], db) -> Optional[str]:
    """Look up company_id from a Stripe customer ID in company_subscriptions."""
    if not stripe_customer_id:
        return None
    result = (
        db.table("company_subscriptions")
        .select("company_id")
        .eq("stripe_customer_id", stripe_customer_id)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["company_id"]
    return None


def _record_billing_event(
    db,
    company_id: str,
    event_type: str,
    amount_usd: Optional[float],
    stripe_event_id: Optional[str],
    metadata: dict,
) -> None:
    """Insert a billing_events row. Silently ignores duplicate stripe_event_id."""
    try:
        row: dict = {
            "company_id": company_id,
            "event_type": event_type,
            "amount_usd": amount_usd,
            "stripe_event_id": stripe_event_id,
            "metadata": metadata,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        db.table("billing_events").insert(row).execute()
    except Exception as exc:
        # Duplicate stripe_event_id triggers a unique constraint — safe to ignore
        log.debug("billing_events insert skipped (likely duplicate): %s", exc)
