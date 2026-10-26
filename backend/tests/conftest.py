"""
Shared pytest fixtures for the SecretaryAI test suite.

Provides:
  - mock_adapter        : AsyncMock UnifiedDataAdapter with deterministic sample data
  - mock_db             : MagicMock Supabase client
  - test_client         : FastAPI TestClient with auth headers
  - sample_customers    : list[Customer]
  - sample_invoices     : list[Invoice]
  - sample_inventory    : list[InventoryItem]
  - company_config      : dict of company settings
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ─── Ensure test env vars exist before app imports ────────────────────────────
os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# A test run is a development environment, not production. Without this the
# suite inherits DEBUG=false (the safe default) and the production billing
# guard in app/config.py correctly refuses to construct Settings, since no
# Stripe keys are configured here. The guard's own behaviour is covered
# explicitly in tests/test_paywall.py, which builds Settings with a cleared
# environment so both branches are exercised.
os.environ.setdefault("DEBUG", "true")


# ─── Paywall ──────────────────────────────────────────────────────────────────
#
# The paid routers in app/main.py are gated behind require_active_subscription.
# That is correct in production and irrelevant to almost every test here: a test
# about RBAC, tenant isolation or idempotency should not also have to stand up a
# believable Stripe subscription, and if it does not, the gate answers 402
# before the behaviour under test ever runs.
#
# So every test gets a subscription that is active, unless it asks otherwise.
# The paywall's own behaviour — who is refused, the trial and grace-period
# cases, which routers are gated, and the production startup guard — is covered
# explicitly and without this override in tests/test_paywall.py, including
# mutation checks proving those assertions fail when the gate is removed.


def _subscription_dependencies():
    """
    Every distinct require_active_subscription callable mounted on the app.

    The factory returns a fresh closure per call site, so there is no single
    function object to override: the router-level ones and the per-endpoint ones
    in app/api/connectors.py are all different objects. Walk the dependency tree
    and collect them.
    """
    from app.main import app
    from fastapi.routing import iter_route_contexts

    found = {}

    def collect(call):
        if callable(call) and "require_active_subscription" in getattr(
            call, "__qualname__", ""
        ):
            found[call] = True

    def walk(dependant):
        if dependant is None:
            return
        collect(getattr(dependant, "call", None))
        for sub in getattr(dependant, "dependencies", []) or []:
            walk(sub)

    # FastAPI >=0.137 preserves router trees instead of flattening app.routes.
    # The effective context contains router-level dependencies (including
    # the subscription gate) that were not attached to the original leaf.
    for context in iter_route_contexts(app.routes):
        walk(getattr(context, "dependant", None))
        for dep in getattr(context, "dependencies", ()) or ():
            collect(getattr(dep, "dependency", None) or getattr(dep, "call", None))
    return list(found)


@pytest.fixture(autouse=True)
def active_subscription():
    """Treat the tenant as a paying customer for the duration of each test."""
    try:
        from app.main import app
    except Exception:  # pragma: no cover - app not importable in this test module
        yield
        return

    deps = _subscription_dependencies()
    added = []
    for dep in deps:
        if dep not in app.dependency_overrides:
            app.dependency_overrides[dep] = lambda: {"status": "active"}
            added.append(dep)
    try:
        yield
    finally:
        for dep in added:
            app.dependency_overrides.pop(dep, None)


# ─── Data fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_customers():
    from app.connectors.base import Customer
    return [
        Customer(
            id="cust-1", qb_id="QB001", name="Acme Corp",
            email="buyer@acme.com", phone="555-1234", state="CA",
            balance=Decimal("15000.00"), total_sales=Decimal("120000.00"),
        ),
        Customer(
            id="cust-2", qb_id="QB002", name="Global Imports LLC",
            email="po@globalimports.com", phone="555-5678", state="TX",
            balance=Decimal("3200.00"), total_sales=Decimal("45000.00"),
        ),
        Customer(
            id="cust-3", qb_id="QB003", name="Metro Distributors",
            email="orders@metro.com", phone="555-9012", state="NY",
            balance=Decimal("0.00"), total_sales=Decimal("8000.00"),
        ),
    ]


@pytest.fixture
def sample_invoices():
    from app.connectors.base import Invoice
    today = date.today()
    return [
        Invoice(
            id="inv-1", qb_id="INV001", customer_id="QB001", customer_name="Acme Corp",
            date=today - timedelta(days=5), due_date=today + timedelta(days=25),
            total=Decimal("8500.00"), balance=Decimal("8500.00"),
            status="open",
            line_items=[
                {"name": "Widget A", "quantity": 100, "unit_price": 50.0, "amount": 5000.0},
                {"name": "Widget B", "quantity": 70, "unit_price": 50.0, "amount": 3500.0},
            ],
        ),
        Invoice(
            id="inv-2", qb_id="INV002", customer_id="QB002", customer_name="Global Imports LLC",
            date=today - timedelta(days=12), due_date=today + timedelta(days=18),
            total=Decimal("3200.00"), balance=Decimal("3200.00"),
            status="open",
            line_items=[
                {"name": "Gadget X", "quantity": 40, "unit_price": 80.0, "amount": 3200.0},
            ],
        ),
        Invoice(
            id="inv-3", qb_id="INV003", customer_id="QB001", customer_name="Acme Corp",
            date=today - timedelta(days=45), due_date=today - timedelta(days=15),
            total=Decimal("6200.00"), balance=Decimal("0.00"),
            status="paid",
            line_items=[
                {"name": "Widget A", "quantity": 124, "unit_price": 50.0, "amount": 6200.0},
            ],
        ),
    ]


@pytest.fixture
def sample_inventory():
    from app.connectors.base import InventoryItem
    return [
        InventoryItem(
            id="item-1", qb_id="ITEM001", name="Widget A", sku="WGT-A",
            quantity_on_hand=Decimal("250"), unit_price=Decimal("50.00"),
            purchase_cost=Decimal("22.50"), reorder_point=Decimal("100"),
        ),
        InventoryItem(
            id="item-2", qb_id="ITEM002", name="Widget B", sku="WGT-B",
            quantity_on_hand=Decimal("30"), unit_price=Decimal("50.00"),
            purchase_cost=Decimal("20.00"), reorder_point=Decimal("80"),
        ),
        InventoryItem(
            id="item-3", qb_id="ITEM003", name="Gadget X", sku="GDG-X",
            quantity_on_hand=Decimal("5"), unit_price=Decimal("80.00"),
            purchase_cost=Decimal("35.00"), reorder_point=Decimal("40"),
        ),
    ]


@pytest.fixture
def company_config():
    return {
        "id": "company-test-uuid",
        "name": "Test Company",
        "qb_type": None,  # no real QB connection in tests
        "preferred_language": "English",
        "alert_email": "admin@testcompany.com",
        "owner_user_id": "user-test-uuid",
        "qb_connection_status": "connected",
    }


# ─── Adapter mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_adapter(sample_customers, sample_invoices, sample_inventory):
    """
    AsyncMock UnifiedDataAdapter that returns deterministic sample data.
    Tests that don't need specific data can use this directly.
    Tests needing custom data can override individual methods.
    """
    adapter = AsyncMock()

    adapter.get_all_customers.return_value = sample_customers
    adapter.get_orders_last_n_days.return_value = sample_invoices
    adapter.get_inventory_qb.return_value = sample_inventory
    adapter.get_inventory_merged.return_value = [
        {
            "qb_id": item.qb_id,
            "product_name": item.name,
            "sku": item.sku,
            "qb_qty": float(item.quantity_on_hand),
            "warehouse_qty": 0,
            "total_qty": float(item.quantity_on_hand),
            "reorder_point": float(item.reorder_point) if item.reorder_point else None,
            "unit_price": float(item.unit_price),
            "purchase_cost": float(item.purchase_cost),
            "source": "qb",
            "weekly_sell_rate": 10.0,
        }
        for item in sample_inventory
    ]
    adapter.get_purchase_orders.return_value = []
    adapter.test_all_connections.return_value = {"quickbooks": True}

    return adapter


# ─── DB mock ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """MagicMock Supabase client — all table/select/execute calls return empty."""
    db = MagicMock()
    # Chain: db.table("x").select("*").eq(...).execute() → returns .data = []
    table_mock = MagicMock()
    table_mock.select.return_value = table_mock
    table_mock.insert.return_value = table_mock
    table_mock.update.return_value = table_mock
    table_mock.upsert.return_value = table_mock
    table_mock.delete.return_value = table_mock
    table_mock.eq.return_value = table_mock
    table_mock.neq.return_value = table_mock
    table_mock.execute.return_value = MagicMock(data=[], count=0)
    db.table.return_value = table_mock
    return db


# ─── FastAPI test client ───────────────────────────────────────────────────────

@pytest.fixture
def test_client():
    """
    FastAPI TestClient.
    Patches get_settings() so no real env file is needed.
    Auth headers contain a pre-baked JWT for company-test-uuid / user-test-uuid.
    """
    from unittest.mock import MagicMock
    from app.config import Settings

    mock_settings = MagicMock(spec=Settings)
    mock_settings.anthropic_api_key = "test-key"
    mock_settings.claude_model = "claude-haiku-4-5-20251001"
    mock_settings.supabase_url = "https://test.supabase.co"
    mock_settings.supabase_anon_key = "test-anon"
    mock_settings.supabase_service_role_key = "test-service-role"
    mock_settings.database_url = "postgresql://test:test@localhost/test"
    mock_settings.secret_key = "test-secret-key-32-chars-long!!x"
    mock_settings.redis_url = "redis://localhost:6379/0"
    mock_settings.sendgrid_api_key = ""
    mock_settings.from_email = "test@test.com"
    mock_settings.sentry_dsn = ""
    mock_settings.access_token_expire_minutes = 60
    mock_settings.intuit_client_id = "test-intuit-id"
    mock_settings.intuit_client_secret = "test-intuit-secret"
    mock_settings.intuit_redirect_uri = "http://localhost:8000/auth/qbo/callback"
    mock_settings.intuit_environment = "sandbox"
    mock_settings.computer_use_enabled = False
    mock_settings.debug = True

    with patch("app.config.get_settings", return_value=mock_settings):
        from app.main import app
        client = TestClient(app, raise_server_exceptions=True)

        # Build a real JWT for tests
        from app.auth.jwt import create_access_token
        token = create_access_token({
            "sub": "user-test-uuid",
            "company_id": "company-test-uuid",
            "role": "owner",
        })
        client.headers.update({"Authorization": f"Bearer {token}"})
        yield client


# ─── Misc helpers ─────────────────────────────────────────────────────────────

@pytest.fixture
def anyio_backend():
    """Use asyncio for anyio-based async tests."""
    return "asyncio"
