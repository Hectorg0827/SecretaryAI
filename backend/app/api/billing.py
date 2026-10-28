"""Billing and subscription management API."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission
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

def _subscription_period(subscription: dict) -> tuple[str, str]:
    """Read Acacia periods or Basil-and-later single-item periods.

    SecretaryAI sells one plan per subscription. Do not invent one entitlement
    period from mixed-interval items; retry for operator review instead.
    """
    period = subscription
    if period.get("current_period_start") is None or period.get("current_period_end") is None:
        items = (subscription.get("items") or {}).get("data") or []
        if len(items) != 1:
            raise ValueError("Expected one subscription item with a billing period")
        period = items[0]
    return tuple(
        datetime.fromtimestamp(period[key], tz=timezone.utc).isoformat()
        for key in ("current_period_start", "current_period_end")
    )


def _invoice_subscription_id(invoice: dict) -> Optional[str]:
    subscription = invoice.get("subscription")
    if not subscription:
        parent = invoice.get("parent") or {}
        if parent.get("type") == "subscription_details":
            subscription = (parent.get("subscription_details") or {}).get("subscription")
    return subscription.get("id") if isinstance(subscription, dict) else subscription

# Plain-language, constant client messages. Provider exception text is never
# echoed to the client or written to logs: it can carry request IDs, customer
# identifiers or attacker-influenced strings.
_PAYMENTS_NOT_SET_UP = "Online payment is not set up yet. Nothing was charged."
_BILLING_RECORDS_UNAVAILABLE = (
    "Billing records are unavailable right now. Nothing was charged or changed. "
    "Please try again in a few minutes."
)
_CHECKOUT_PROVIDER_FAILED = (
    "The payment service could not start checkout. Nothing was charged. "
    "Please try again in a few minutes."
)
_PORTAL_PROVIDER_FAILED = (
    "The payment service could not open the subscription page. Nothing was changed. "
    "Please try again in a few minutes."
)
# A subscription in these states is still billed by Stripe; a second checkout
# would start a second, parallel subscription and charge the company twice.
_BILLED_STATUSES = ("active", "trialing", "past_due")


def _require_billing_owner(user: dict = Depends(require_permission("manage_billing"))) -> dict:
    """Payment changes are owner-only.

    ``require_permission`` lets every role holding ``view_all`` (managers too)
    pass any check, so the owner role is enforced explicitly as well.
    """
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Only the account owner can manage billing")
    return user


def _token_company_id(user: dict) -> str:
    """Tenant comes only from the verified token, never from the request body."""
    company_id = user.get("company_id")
    if not company_id:
        raise HTTPException(status_code=400, detail="User has no associated company")
    return company_id


def _return_base_url() -> str:
    """Where Stripe sends the browser back to. Never guess in production."""
    if settings.frontend_url:
        return settings.frontend_url.rstrip("/")
    if settings.debug:
        return "http://localhost:5173"
    log.error("FRONTEND_URL is not set; Stripe return URL unavailable")
    raise HTTPException(status_code=503, detail=_PAYMENTS_NOT_SET_UP)


def _read_rows(query) -> list[dict]:
    try:
        rows = query.execute().data
    except Exception as exc:
        log.error("Billing database read failed")
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE) from exc
    # Fail closed: an unreadable result is not "no subscription", which would
    # let a second checkout through.
    if not isinstance(rows, list):
        log.error("Billing database returned an unexpected result")
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE)
    return rows


def _latest_subscription(company_id: str, db) -> Optional[dict]:
    rows = _read_rows(
        db.table("company_subscriptions")
        .select("status,stripe_customer_id,stripe_subscription_id")
        .eq("company_id", company_id)
        .order("created_at", desc=True)
        .limit(1)
    )
    return rows[0] if rows else None


def _https_url(value) -> Optional[str]:
    return value if isinstance(value, str) and value.startswith("https://") else None


def _get_or_create_stripe_customer(company_id: str, company_email: str, existing_customer_id: Optional[str]) -> Optional[str]:
    """Return the company's existing Stripe customer or create a new one."""
    if not _stripe_available:
        return None
    if existing_customer_id:
        return existing_customer_id

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
    user: dict = Depends(_require_billing_owner),
    db=Depends(get_db),
):
    """
    Create a Stripe Checkout session for the requested plan (owner only).
    Returns checkout_url and session_id. Nothing is charged here: the owner
    confirms and pays on Stripe's page, and the subscription is recorded only
    when Stripe's signed webhook arrives.
    """
    company_id = _token_company_id(user)

    if body.billing_period not in ("monthly", "yearly"):
        raise HTTPException(status_code=422, detail="Choose monthly or yearly billing")

    # No mock checkout URL: a fake link would look like a working payment flow.
    if not _stripe_available:
        log.warning("Stripe not configured; checkout refused")
        raise HTTPException(status_code=503, detail=_PAYMENTS_NOT_SET_UP)

    plans = _read_rows(
        db.table("subscription_plans")
        .select("id,stripe_price_id_monthly,stripe_price_id_yearly")
        .eq("id", body.plan_id)
        .limit(1)
    )
    if not plans:
        raise HTTPException(status_code=404, detail="That plan is not available")
    plan = plans[0]

    price_id = _resolve_price_id(plan, body.billing_period)
    if not price_id:
        raise HTTPException(
            status_code=400,
            detail="This plan cannot be bought online yet for that billing period",
        )

    current = _latest_subscription(company_id, db)
    if current and current.get("stripe_subscription_id") and current.get("status") in _BILLED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This account already has a subscription. Use Manage subscription "
                "to change the plan or payment details."
            ),
        )

    # Fetch company email for customer creation
    companies = _read_rows(db.table("companies").select("email").eq("id", company_id).limit(1))
    company_email = (companies[0].get("email") or "") if companies else ""

    base_url = _return_base_url()

    try:
        customer_id = _get_or_create_stripe_customer(
            company_id, company_email, (current or {}).get("stripe_customer_id"),
        )

        session_kwargs: dict = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": f"{base_url}/settings?billing=success",
            "cancel_url": f"{base_url}/settings?billing=cancelled",
            "metadata": {
                "company_id": company_id,
                "plan_id": plan["id"],
                "billing_period": body.billing_period,
            },
        }
        if customer_id:
            session_kwargs["customer"] = customer_id

        session = _stripe.checkout.Session.create(**session_kwargs)
        checkout_url = _https_url(getattr(session, "url", None))
        session_id = getattr(session, "id", None)
    except Exception as exc:
        log.error("Stripe checkout session creation failed")
        raise HTTPException(status_code=502, detail=_CHECKOUT_PROVIDER_FAILED) from exc

    if not checkout_url:
        log.error("Stripe checkout session returned no usable URL")
        raise HTTPException(status_code=502, detail=_CHECKOUT_PROVIDER_FAILED)

    return {"checkout_url": checkout_url, "session_id": session_id}


@router.post("/portal")
async def create_portal_session(
    user: dict = Depends(_require_billing_owner),
    db=Depends(get_db),
):
    """
    Create a Stripe Customer Portal session so the owner can manage the
    subscription and payment details on Stripe. Returns portal_url.
    """
    company_id = _token_company_id(user)

    if not _stripe_available:
        log.warning("Stripe not configured; portal refused")
        raise HTTPException(status_code=503, detail=_PAYMENTS_NOT_SET_UP)

    current = _latest_subscription(company_id, db)
    if not current or not current.get("stripe_customer_id"):
        raise HTTPException(
            status_code=404,
            detail="There is no paid subscription to manage yet. Choose a plan to subscribe.",
        )

    base_url = _return_base_url()

    try:
        portal_session = _stripe.billing_portal.Session.create(
            customer=current["stripe_customer_id"],
            return_url=f"{base_url}/settings?billing=portal",
        )
        portal_url = _https_url(getattr(portal_session, "url", None))
    except Exception as exc:
        log.error("Stripe portal session creation failed")
        raise HTTPException(status_code=502, detail=_PORTAL_PROVIDER_FAILED) from exc

    if not portal_url:
        log.error("Stripe portal session returned no usable URL")
        raise HTTPException(status_code=502, detail=_PORTAL_PROVIDER_FAILED)

    return {"portal_url": portal_url}


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

    if settings.secretaryai_deployment_env.strip().lower() == "staging" and event.get("livemode") is not False:
        raise HTTPException(status_code=400, detail="Staging accepts only test-mode Stripe events")

    log.info("Stripe webhook received")

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
            log.debug("Stripe webhook: unhandled event type")

    except Exception as exc:
        log.error("Stripe webhook handler failed")
        # Acknowledge only successful handling so Stripe retries transient errors.
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

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
        stripe_sub = _stripe.Subscription.retrieve(stripe_subscription_id)
        period_start, period_end = _subscription_period(stripe_sub)

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
    stripe_subscription_id = _invoice_subscription_id(invoice)
    amount_paid = (invoice.get("amount_paid") or 0) / 100  # cents → dollars

    company_id = _company_id_from_customer(stripe_customer_id, db)
    if not company_id:
        log.warning("invoice.paid: no company found for customer")
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
        log.warning("invoice.payment_failed: no company found for customer")
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
        log.warning("customer.subscription.updated: no company found for customer")
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
    period_start, period_end = _subscription_period(stripe_sub)
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
        log.warning("customer.subscription.deleted: no company found for customer")
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
        if getattr(exc, "code", None) == "23505":
            log.debug("billing_events insert skipped (duplicate Stripe event)")
            return
        raise
