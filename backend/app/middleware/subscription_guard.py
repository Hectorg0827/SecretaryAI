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

from fastapi import Depends, HTTPException

from app.api.deps import get_db
from app.auth.rbac import get_current_user

log = logging.getLogger(__name__)

# Grace period in days before a canceled/past_due subscription blocks access
_GRACE_PERIOD_DAYS = 7


def require_active_subscription():
    """
    FastAPI dependency factory.

    Returns a dependency that:
    - Allows access if subscription status is 'active' or 'trialing'.
    - Allows access within a 7-day grace period after cancellation or past_due.
    - Raises HTTP 402 Payment Required once the grace period has expired.
    - Raises HTTP 402 if no subscription row exists at all.
    """

    def _check(
        user: dict = Depends(get_current_user),
        db=Depends(get_db),
    ) -> dict:
        company_id = user.get("company_id")
        if not company_id:
            raise HTTPException(status_code=400, detail="User has no associated company")

        result = (
            db.table("company_subscriptions")
            .select("status,current_period_end,trial_end,cancel_at_period_end,updated_at")
            .eq("company_id", company_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

        if not result.data:
            raise HTTPException(
                status_code=402,
                detail="No active subscription. Please subscribe to continue.",
            )

        sub = result.data[0]
        status = sub.get("status", "canceled")

        # Trialing — always allow
        if status == "trialing":
            return sub

        # Active — always allow
        if status == "active":
            return sub

        # Canceled or past_due — check grace period
        # Grace window is measured from updated_at (when status last changed)
        updated_at_str = sub.get("updated_at")
        if updated_at_str:
            try:
                if updated_at_str.endswith("Z"):
                    updated_at_str = updated_at_str[:-1] + "+00:00"
                updated_at = datetime.fromisoformat(updated_at_str)
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
                now = datetime.now(tz=timezone.utc)
                elapsed_days = (now - updated_at).days
                if elapsed_days <= _GRACE_PERIOD_DAYS:
                    log.info(
                        "Company %s subscription %s — within %d-day grace period (%d days elapsed)",
                        company_id, status, _GRACE_PERIOD_DAYS, elapsed_days,
                    )
                    return sub
            except (ValueError, TypeError) as exc:
                log.warning("Could not parse subscription updated_at '%s': %s", updated_at_str, exc)

        raise HTTPException(
            status_code=402,
            detail=(
                "Your subscription is no longer active. "
                "Please update your payment method or resubscribe to continue."
            ),
        )

    return _check
