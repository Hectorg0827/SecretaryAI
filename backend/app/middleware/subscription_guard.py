"""
Subscription guard dependency.

Usage:
    from app.middleware.subscription_guard import require_active_subscription

    @router.get("/some-protected-endpoint")
    async def my_endpoint(
        _sub=Depends(require_active_subscription()),
        user: dict = Depends(get_current_user),
        ...
    ):
        ...
"""
import logging
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from app.api.deps import get_db
from app.auth.rbac import get_current_user

log = logging.getLogger(__name__)

# Grace period in days before a canceled/past_due subscription blocks access
_GRACE_PERIOD_DAYS = 7


TRIAL_DAYS = 14
TRIAL_PLAN_ID = "starter"


def start_trial(db, company_id: str) -> None:
    """Give a newly registered company a free trial (idempotent, non-fatal)."""
    from datetime import timedelta
    now = datetime.now(tz=timezone.utc)
    try:
        existing = db.table("company_subscriptions").select("id").eq("company_id", company_id).execute()
        if existing.data:
            return
        db.table("company_subscriptions").insert({
            "company_id": company_id,
            "plan_id": TRIAL_PLAN_ID,
            "status": "trialing",
            "trial_end": (now + timedelta(days=TRIAL_DAYS)).isoformat(),
            "updated_at": now.isoformat(),
        }).execute()
    except Exception as exc:
        log.error("Could not start trial for company %s: %s", company_id, exc)


def _parse_ts(value):
    if not value:
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        ts = datetime.fromisoformat(text)
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def subscription_allows_access(sub: dict | None, now: datetime | None = None) -> bool:
    """
    The paywall rule, in one place:
      active                      → yes
      trialing                    → yes until trial_end (no end date = yes)
      past_due / canceled / other → yes for a 7-day grace period, then no
      no subscription at all      → no
    """
    if not sub:
        return False
    now = now or datetime.now(tz=timezone.utc)
    status = sub.get("status", "canceled")
    if status == "active":
        return True
    if status == "trialing":
        trial_end = _parse_ts(sub.get("trial_end"))
        return trial_end is None or now <= trial_end
    changed = _parse_ts(sub.get("updated_at"))
    return changed is not None and (now - changed).days <= _GRACE_PERIOD_DAYS


def require_active_subscription():
    """
    FastAPI dependency factory — the paywall.

    Does nothing unless BILLING_ENFORCED=true (so turning the paywall on is one
    deliberate switch, made after Stripe is configured). When enforced, a
    company without an active subscription, a running trial or a grace period
    gets HTTP 402 Payment Required.
    """

    def _check(
        request: Request,
        user: dict = Depends(get_current_user),
    ) -> dict:
        from app.config import get_settings
        if not getattr(get_settings(), "billing_enforced", False):
            return {}  # paywall off: no database work at all

        # Resolve the DB only when needed (honouring test overrides).
        db = request.app.dependency_overrides.get(get_db, get_db)()

        company_id = user.get("company_id")
        if not company_id:
            raise HTTPException(status_code=400, detail="User has no associated company")

        try:
            result = (
                db.table("company_subscriptions")
                .select("status,current_period_end,trial_end,cancel_at_period_end,updated_at")
                .eq("company_id", company_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            log.error("Subscription lookup failed for %s: %s", company_id, exc)
            raise HTTPException(status_code=503, detail="Service temporarily unavailable")

        sub = result.data[0] if result.data else None
        if not subscription_allows_access(sub):
            raise HTTPException(
                status_code=402,
                detail=(
                    "Your free trial or subscription has ended. "
                    "The account owner can choose a plan under Settings → Billing."
                ),
            )
        return sub

    return _check
