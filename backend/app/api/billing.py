"""Billing and subscription management API."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.deps import get_db
from app.auth.rbac import get_current_user, require_permission  # noqa: F401  (tests override it)
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


def _get_or_create_stripe_customer(
    company_id: str, company_email: str, existing_customer_id: Optional[str], attempt_id: str,
) -> Optional[str]:
    """Return the company's existing Stripe customer or create a new one."""
    if not _stripe_available:
        return None
    if existing_customer_id:
        return existing_customer_id

    customer = _stripe.Customer.create(
        email=company_email,
        metadata={"company_id": company_id},
        idempotency_key=f"secretaryai-customer-{attempt_id}",
    )
    return customer["id"]


def _billing_rpc(db, name: str, params: dict) -> dict | bool:
    """Call the atomic, service-role-only billing functions; fail closed if absent."""
    try:
        result = db.rpc(name, params).execute().data
    except Exception as exc:
        log.error("Atomic billing database operation failed")
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE) from exc
    if isinstance(result, bool) or isinstance(result, dict):
        return result
    log.error("Atomic billing database returned an unexpected result")
    raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE)


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
    # Billing is shown to the owner only (Settings → Billing).
    user: dict = Depends(_require_billing_owner),
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

    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail=_PAYMENTS_NOT_SET_UP)

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

    reservation = _billing_rpc(db, "billing_reserve_checkout", {
        "p_company_id": company_id, "p_plan_id": plan["id"],
        "p_billing_period": body.billing_period,
    })
    if not isinstance(reservation, dict):
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE)
    if reservation.get("state") in ("subscribed", "completed"):
        raise HTTPException(status_code=409, detail="This account already has a subscription. Use Manage subscription.")
    if reservation.get("state") == "different_plan":
        raise HTTPException(status_code=409, detail="Finish or cancel the existing checkout before choosing another plan.")
    if reservation.get("state") == "ready":
        previous_id = reservation.get("stripe_session_id")
        try:
            previous = _stripe.checkout.Session.retrieve(previous_id)
            previous_status = getattr(previous, "status", None)
        except Exception as exc:
            log.error("Stripe checkout verification failed")
            raise HTTPException(status_code=502, detail=_CHECKOUT_PROVIDER_FAILED) from exc
        if previous_status == "open" and _https_url(reservation.get("checkout_url")):
            return {"checkout_url": reservation["checkout_url"], "session_id": previous_id}
        if previous_status != "expired":
            raise HTTPException(status_code=409, detail="A checkout is already being processed. Please try again shortly.")
        rotated = _billing_rpc(db, "billing_rotate_expired_checkout", {
            "p_company_id": company_id, "p_session_id": previous_id,
        })
        if rotated is not True:
            raise HTTPException(status_code=409, detail="Checkout changed while you were trying again. Please retry.")
        reservation = _billing_rpc(db, "billing_reserve_checkout", {
            "p_company_id": company_id, "p_plan_id": plan["id"],
            "p_billing_period": body.billing_period,
        })
    if not isinstance(reservation, dict) or reservation.get("state") != "reserved":
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE)
    try:
        attempt_id = str(UUID(reservation["attempt_id"]))
        reserved_at = datetime.fromisoformat(reservation["created_at"].replace("Z", "+00:00"))
        if reserved_at.tzinfo is None or reserved_at > datetime.now(tz=timezone.utc):
            raise ValueError("Invalid checkout reservation time")
    except (TypeError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE) from exc
    if datetime.now(tz=timezone.utc) - reserved_at >= timedelta(hours=20):
        # Stripe may forget idempotency keys after 24 hours. An uncertain older
        # attempt must be reconciled manually, not retried into another session.
        raise HTTPException(status_code=409, detail="Checkout requires support review before another payment can begin.")

    # Fetch company email for customer creation
    companies = _read_rows(db.table("companies").select("email").eq("id", company_id).limit(1))
    company_email = (companies[0].get("email") or "") if companies else ""

    base_url = _return_base_url()

    try:
        customer_id = _get_or_create_stripe_customer(
            company_id, company_email,
            reservation.get("stripe_customer_id") or (current or {}).get("stripe_customer_id"),
            attempt_id,
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
                "checkout_attempt_id": attempt_id,
            },
            "idempotency_key": f"secretaryai-checkout-{attempt_id}",
        }
        if customer_id:
            session_kwargs["customer"] = customer_id

        session = _stripe.checkout.Session.create(**session_kwargs)
        checkout_url = _https_url(getattr(session, "url", None))
        session_id = getattr(session, "id", None)
    except Exception as exc:
        log.error("Stripe checkout session creation failed")
        raise HTTPException(status_code=502, detail=_CHECKOUT_PROVIDER_FAILED) from exc

    if not checkout_url or not isinstance(session_id, str) or not session_id:
        log.error("Stripe checkout session returned no usable URL")
        raise HTTPException(status_code=502, detail=_CHECKOUT_PROVIDER_FAILED)

    finalized = _billing_rpc(db, "billing_finalize_checkout", {
        "p_company_id": company_id, "p_attempt_id": attempt_id,
        "p_customer_id": customer_id, "p_session_id": session_id,
        "p_checkout_url": checkout_url,
    })
    if finalized is not True:
        raise HTTPException(status_code=503, detail=_BILLING_RECORDS_UNAVAILABLE)

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

    if not settings.billing_enabled:
        raise HTTPException(status_code=503, detail=_PAYMENTS_NOT_SET_UP)

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
    # Billing is shown to the owner only (Settings → Billing).
    user: dict = Depends(_require_billing_owner),
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
        if event_type in (
            "checkout.session.completed", "invoice.paid", "invoice.payment_failed",
            "customer.subscription.updated", "customer.subscription.deleted",
        ):
            _reconcile_billing_event(event_type, event_data, stripe_event_id, db)

    except Exception as exc:
        log.error("Stripe webhook handler failed")
        # Acknowledge only successful handling so Stripe retries transient errors.
        raise HTTPException(status_code=500, detail="Webhook processing failed") from exc

    return {"received": True}


def _reconcile_billing_event(event_type: str, obj: dict, stripe_event_id: str, db) -> None:
    """Retrieve Stripe's present state, then atomically apply it and ledger the event.

    Out-of-order invoices must never override a newer Stripe subscription state.
    The database RPC serializes each company and rejects stale provider snapshots.
    """
    checkout = event_type == "checkout.session.completed"
    if checkout:
        metadata = obj.get("metadata") or {}
        company_id, plan_id = metadata.get("company_id"), metadata.get("plan_id")
        subscription_id = obj.get("subscription")
        customer_id = obj.get("customer")
        if not company_id or not plan_id or not subscription_id or not customer_id:
            raise ValueError("Checkout missing mandatory Stripe identifiers")
        attempt_id = metadata.get("checkout_attempt_id")
        if attempt_id is not None:
            attempt_id = str(UUID(attempt_id))
    else:
        plan_id = None
        subscription_id = (
            _invoice_subscription_id(obj) if event_type.startswith("invoice.") else obj.get("id")
        )
        customer_id = obj.get("customer")
        if not subscription_id:
            # A one-off invoice is not a SecretaryAI subscription event.
            if event_type.startswith("invoice."):
                return
            raise ValueError("Subscription webhook missing a subscription ID")
        company_id = _company_id_from_customer(customer_id, db)
        if not company_id:
            # Stripe may deliver invoice.paid before checkout.session.completed.
            # Force a retry; never acknowledge and silently discard a payment.
            raise ValueError("Webhook arrived before company subscription association")

    requested_at = datetime.now(tz=timezone.utc).isoformat()
    stripe_sub = _stripe.Subscription.retrieve(subscription_id)
    if stripe_sub.get("customer") != customer_id or stripe_sub.get("id") != subscription_id:
        raise ValueError("Stripe subscription identity mismatch")

    status_map = {
        "active": "active", "past_due": "past_due", "canceled": "canceled",
        "trialing": "trialing", "incomplete": "past_due",
        "incomplete_expired": "canceled", "unpaid": "past_due", "paused": "past_due",
    }
    if stripe_sub.get("status") not in status_map:
        raise ValueError("Unknown Stripe subscription status")
    status = status_map[stripe_sub["status"]]
    period_start, period_end = _subscription_period(stripe_sub)
    trial_end = (
        datetime.fromtimestamp(stripe_sub["trial_end"], tz=timezone.utc).isoformat()
        if stripe_sub.get("trial_end") else None
    )

    # Price metadata is authoritative for customer portal plan changes.
    items = (stripe_sub.get("items") or {}).get("data") or []
    if len(items) == 1:
        price = (items[0].get("price") or {}).get("id")
        if price:
            for field in ("stripe_price_id_monthly", "stripe_price_id_yearly"):
                matches = _read_rows(db.table("subscription_plans").select("id").eq(field, price).limit(1))
                if matches:
                    plan_id = matches[0]["id"]
                    break
    amount_usd = None
    if event_type.startswith("invoice."):
        amount_key = "amount_paid" if event_type == "invoice.paid" else "amount_due"
        amount_usd = (obj.get(amount_key) or 0) / 100
    financial_object_id = obj.get("id") if event_type in (
        "invoice.paid", "checkout.session.completed",
    ) else None
    if event_type in ("invoice.paid", "checkout.session.completed") and not financial_object_id:
        raise ValueError("Financial webhook missing Stripe object ID")
    result = _billing_rpc(db, "billing_apply_stripe_event", {
        "p_company_id": company_id, "p_event_id": stripe_event_id,
        "p_event_type": event_type, "p_customer_id": customer_id,
        "p_subscription_id": subscription_id, "p_plan_id": plan_id,
        "p_status": status, "p_period_start": period_start,
        "p_period_end": period_end, "p_trial_end": trial_end,
        "p_cancel_at_period_end": bool(stripe_sub.get("cancel_at_period_end", False)),
        "p_amount_usd": amount_usd,
        "p_metadata": {"stripe_subscription_id": subscription_id,
                       "stripe_invoice_id": obj.get("id") if event_type.startswith("invoice.") else None},
        "p_retrieved_at": requested_at,
        "p_checkout_session_id": obj.get("id") if checkout else None,
        "p_checkout_attempt_id": attempt_id if checkout else None,
        "p_object_id": financial_object_id,
    })
    if not isinstance(result, dict) or not isinstance(result.get("duplicate"), bool):
        raise ValueError("Invalid atomic billing response")


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
