"""
The subscription paywall.

`require_active_subscription` existed in app/middleware/subscription_guard.py
and was applied to exactly zero routes, so every account that signed up got
the entire product for free. These tests cover both halves of fixing that:

  * the boundary itself — which routers are gated and which are deliberately
    not, asserted structurally so a newly mounted router cannot quietly land on
    the wrong side of it;
  * the behaviour — an unpaid account is refused, a paying one is not, and the
    trial and grace-period cases in between.

Plus the startup guard: a production deployment with billing enabled but
Stripe unconfigured must refuse to boot rather than run in mock mode.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends

from app.config import Settings
from app.middleware.subscription_guard import _GRACE_PERIOD_DAYS, require_active_subscription


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sub_db(rows: list[dict] | None):
    """A db double that answers the guard's company_subscriptions lookup."""
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=rows)
    db.table.return_value = chain
    return db


def _row(status: str, *, updated_days_ago: int = 0) -> dict:
    ts = datetime.now(tz=timezone.utc) - timedelta(days=updated_days_ago)
    return {
        "status": status,
        "current_period_end": None,
        "trial_end": None,
        "cancel_at_period_end": False,
        "updated_at": ts.isoformat().replace("+00:00", "Z"),
    }


USER = {"company_id": "company-test-uuid", "sub": "user-test-uuid", "role": "owner"}


def _check(rows, user=USER):
    """Invoke the dependency the way FastAPI would, with deps resolved."""
    return require_active_subscription()(user=user, db=_sub_db(rows))


# ── Behaviour: who gets in ────────────────────────────────────────────────────

class TestSubscriptionDecision:
    def test_no_subscription_row_is_refused(self):
        """A brand-new signup that never paid. This is the case that was free."""
        with pytest.raises(Exception) as exc:
            _check([])
        assert exc.value.status_code == 402
        assert "subscribe" in exc.value.detail.lower()

    def test_null_result_is_refused(self):
        with pytest.raises(Exception) as exc:
            _check(None)
        assert exc.value.status_code == 402

    @pytest.mark.parametrize("status", ["active", "trialing"])
    def test_paying_and_trialing_accounts_are_allowed(self, status):
        assert _check([_row(status)])["status"] == status

    @pytest.mark.parametrize("status", ["canceled", "past_due"])
    def test_lapsed_account_is_allowed_inside_the_grace_period(self, status):
        """A failed card must not lock a customer out on the same day."""
        assert _check([_row(status, updated_days_ago=_GRACE_PERIOD_DAYS - 1)])["status"] == status

    @pytest.mark.parametrize("status", ["canceled", "past_due"])
    def test_lapsed_account_is_refused_after_the_grace_period(self, status):
        with pytest.raises(Exception) as exc:
            _check([_row(status, updated_days_ago=_GRACE_PERIOD_DAYS + 1)])
        assert exc.value.status_code == 402

    def test_unparseable_updated_at_is_refused_rather_than_admitted(self):
        """Fail closed: a row we cannot date must not grant indefinite access."""
        bad = _row("canceled")
        bad["updated_at"] = "not-a-timestamp"
        with pytest.raises(Exception) as exc:
            _check([bad])
        assert exc.value.status_code == 402

    def test_user_without_a_company_is_rejected(self):
        with pytest.raises(Exception) as exc:
            _check([_row("active")], user={"sub": "u1"})
        assert exc.value.status_code == 400


# ── The boundary: which routes are gated ──────────────────────────────────────

def _group(path: str) -> str:
    """
    The router a path belongs to.

    /api/* routers are mounted one level deep (/api/chat/...), while /auth and
    /webhooks are mounted at the top level, so they need different depths —
    taking a fixed three segments turns /auth/login into its own "router".
    """
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    if parts[0] == "api":
        return "/api/" + parts[1] if len(parts) > 1 else "/api"
    return "/" + parts[0]


def _gated_prefixes() -> set[str]:
    """Route groups carrying the subscription dependency, read off the live app."""
    from app.main import app

    gated = set()
    for route in app.routes:
        # Newer FastAPI exposes the *solved* dependency graph through
        # APIRoute.dependant.dependencies (where the target is .call).
        # Legacy FastAPI also exposes the unsolved Depends(.dependency).
        # Inspect both and require the exact subscription closure, rather
        # than mistaking framework internals for an ungated paid endpoint.
        raw = getattr(route, "dependencies", ()) or ()
        compiled = getattr(getattr(route, "dependant", None), "dependencies", ()) or ()
        dependencies = [
            getattr(d, "dependency", None) or getattr(d, "call", None)
            for d in (*raw, *compiled)
        ]
        if any(
            callable(fn) and "require_active_subscription" in getattr(fn, "__qualname__", "")
            for fn in dependencies
        ):
            gated.add(_group(getattr(route, "path", "")))
    return gated


# Everything a customer pays for.
PAID = [
    "/api/chat", "/api/dashboard", "/api/accounts", "/api/inventory",
    "/api/actions", "/api/agent", "/api/notifications", "/api/inbox",
    "/api/workflows", "/api/feed", "/api/computer-use", "/api/logistics",
    "/api/compliance",
]

# Free on purpose. Each entry would break something specific if gated.
FREE = {
    "/auth": "you cannot sign in to pay if signing in requires having paid",
    "/api/billing": "gating checkout locks out the customer trying to subscribe",
    "/api/setup": "onboarding happens before a subscription exists",
    "/api/settings": "a lapsed account must still be able to cancel",
    "/webhooks": "Stripe and Intuit call these and hold no user JWT",
    "/api/admin": "internal; protected by RBAC, not by subscription",
    "/api/docs": "X-Docs-Key-gated API reference, not a customer feature",
    "/api/connectors": (
        "not gated at the router level because the agent authenticates with a "
        "connector token, not a user JWT; its user-facing endpoints are gated "
        "individually — asserted by TestConnectorEndpointsSplit below"
    ),
}


class TestPaywallBoundary:
    @pytest.mark.parametrize("prefix", PAID)
    def test_paid_router_is_gated(self, prefix):
        assert prefix in _gated_prefixes(), (
            f"{prefix} is NOT behind require_active_subscription — it is being "
            f"served free. Gate it in app/main.py or move it to FREE here with "
            f"a reason."
        )

    @pytest.mark.parametrize("prefix", sorted(FREE))
    def test_free_router_is_not_gated(self, prefix):
        assert prefix not in _gated_prefixes(), (
            f"{prefix} must stay free: {FREE[prefix]}"
        )

    def test_every_mounted_api_router_has_been_classified(self):
        """
        Catches the real risk: someone mounts /api/newthing and never decides
        whether it is paid. Without this, a new router defaults to free and
        nobody notices.
        """
        from app.main import app

        mounted = {
            _group(getattr(r, "path", ""))
            for r in app.routes
            if getattr(r, "path", "").startswith(("/api/", "/auth", "/webhooks"))
        }
        classified = set(PAID) | set(FREE)
        unclassified = {m for m in mounted if m and m not in classified}
        assert not unclassified, (
            f"These route groups are mounted but not classified as paid or free: "
            f"{sorted(unclassified)}. Add each to PAID or to FREE (with a reason) "
            f"in tests/test_paywall.py, and gate it in app/main.py if it is paid."
        )


class TestConnectorEndpointsSplit:
    """
    The connectors router is NOT gated wholesale: the agent authenticates with a
    connector token, not a user JWT, so a user-subscription check would reject
    it with a 401 it cannot act on. The endpoints a person calls are gated
    individually instead.
    """

    def _params(self, func_name: str) -> list[str]:
        import inspect

        from app.api import connectors

        sig = inspect.signature(getattr(connectors, func_name))
        out = []
        for name, p in sig.parameters.items():
            dep = getattr(p.default, "dependency", None)
            out.append(getattr(dep, "__qualname__", "") if dep else "")
        return out

    @pytest.mark.parametrize("func", [
        "register_connector", "get_connector_status",
        "get_single_connector_status", "generate_install_secret", "dispatch_task",
    ])
    def test_user_facing_connector_endpoints_are_gated(self, func):
        assert any("require_active_subscription" in q for q in self._params(func)), (
            f"{func} is called by a person and must require an active subscription"
        )

    @pytest.mark.parametrize("func", [
        "connector_heartbeat", "fetch_connector_tasks", "submit_task_result",
        "report_sync_status", "report_connector_error",
    ])
    def test_machine_facing_connector_endpoints_are_not_gated(self, func):
        assert not any("require_active_subscription" in q for q in self._params(func)), (
            f"{func} is called by the connector agent with a connector token; a "
            f"user-subscription dependency would 401 it"
        )


# ── Startup guard: billing must not be half-configured in production ──────────

class TestBillingStartupGuard:
    REQUIRED = {
        "SECRET_KEY": "x" * 64,
        "SUPABASE_URL": "https://x.supabase.co",
        "SUPABASE_ANON_KEY": "k",
        "SUPABASE_SERVICE_ROLE_KEY": "k",
        "DATABASE_URL": "postgresql://u:p@h:5432/d",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }

    def _settings(self, **overrides) -> Settings:
        env = {**self.REQUIRED, **overrides}
        with patch.dict(os.environ, env, clear=True):
            return Settings(_env_file=None)

    def test_production_with_billing_on_and_no_stripe_key_refuses_to_start(self):
        with pytest.raises(ValueError) as exc:
            self._settings(DEBUG="false")
        assert "STRIPE_SECRET_KEY" in str(exc.value)

    def test_production_without_webhook_secret_refuses_to_start(self):
        """Without it, subscriptions never activate and paying customers are
        still refused — the worst possible failure, because money moves."""
        with pytest.raises(ValueError) as exc:
            self._settings(DEBUG="false", STRIPE_SECRET_KEY="sk_live_x")
        assert "STRIPE_WEBHOOK_SECRET" in str(exc.value)

    def test_production_with_billing_fully_configured_starts(self):
        s = self._settings(
            DEBUG="false", STRIPE_SECRET_KEY="sk_live_x", STRIPE_WEBHOOK_SECRET="whsec_x"
        )
        assert s.billing_enabled is True

    def test_production_with_billing_explicitly_disabled_starts(self):
        s = self._settings(DEBUG="false", BILLING_ENABLED="false")
        assert s.billing_enabled is False

    def test_debug_mode_does_not_require_stripe(self):
        s = self._settings(DEBUG="true")
        assert s.debug is True

    def test_billing_is_enabled_by_default(self):
        """The safe default is to charge, not to give the product away."""
        assert self._settings(DEBUG="true").billing_enabled is True
