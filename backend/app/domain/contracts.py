"""
Canonical domain contracts.

These are the authoritative typed representations of core business entities.
Every connector, adapter, API router, and AI module should convert to/from
these types rather than passing raw dicts.

Design principles:
  - All fields use snake_case and match the Supabase column names exactly.
  - Optional fields are typed as X | None, never omitted.
  - Monetary values are Decimal, not float (avoids rounding errors).
  - Dates are date objects; datetimes are timezone-aware.
  - All IDs are str (UUIDs serialise cleanly as strings).

Adding a field:
  1. Add it here with the correct type.
  2. Update the corresponding migration.
  3. Update the relevant connector's to_domain() / from_domain() methods.
  4. Update any API response schemas that expose this type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


# ─── Enums ────────────────────────────────────────────────────────────────────

class AccountStatus(str, Enum):
    ACTIVE   = "active"
    INACTIVE = "inactive"
    PROSPECT = "prospect"
    CHURNED  = "churned"


class OrderStatus(str, Enum):
    DRAFT     = "draft"
    OPEN      = "open"
    PAID      = "paid"
    OVERDUE   = "overdue"
    VOID      = "void"
    PARTIAL   = "partial"


class InventoryStatus(str, Enum):
    IN_STOCK    = "in_stock"
    LOW_STOCK   = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"


class ActionClass(str, Enum):
    """Risk classification for any action proposed by the AI or automation."""
    READ        = "read"
    DRAFT       = "draft"
    COMMIT      = "commit"
    DESTRUCTIVE = "destructive"


class DataPath(str, Enum):
    """Which data access path produced a result."""
    API          = "api"
    BROWSER      = "browser"
    FILE         = "file"
    COMPUTER_USE = "computer_use"
    CACHE        = "cache"
    UNKNOWN      = "unknown"


# ─── Core entities ────────────────────────────────────────────────────────────

@dataclass
class Customer:
    """
    Canonical customer / account record.
    Maps to: accounts table, QB customers, CRM contacts.
    """
    id: str                                  # UUID (Supabase PK)
    company_id: str
    external_id: str | None                  # QB customer ID
    name: str
    email: str | None = None
    phone: str | None = None
    billing_address: str | None = None
    shipping_address: str | None = None
    territory: str | None = None
    sales_rep_id: str | None = None
    status: AccountStatus = AccountStatus.ACTIVE
    balance: Decimal = Decimal(0)
    credit_limit: Decimal | None = None
    last_order_date: date | None = None
    total_ytd: Decimal = Decimal(0)
    health_score: int | None = None          # 0-100
    health_label: str | None = None          # 'good' | 'at_risk' | 'churned'
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: DataPath = DataPath.UNKNOWN


@dataclass
class Invoice:
    """
    Canonical invoice / order record.
    Maps to: orders table, QB invoices.
    """
    id: str
    company_id: str
    external_id: str | None                  # QB invoice ID / transaction ID
    customer_id: str
    customer_name: str | None = None
    invoice_number: str | None = None
    status: OrderStatus = OrderStatus.OPEN
    issue_date: date | None = None
    due_date: date | None = None
    paid_date: date | None = None
    subtotal: Decimal = Decimal(0)
    tax_amount: Decimal = Decimal(0)
    total: Decimal = Decimal(0)
    balance: Decimal = Decimal(0)            # amount still owed
    currency: str = "USD"
    memo: str | None = None
    line_items: list[LineItem] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: DataPath = DataPath.UNKNOWN


@dataclass
class LineItem:
    """Single line on an invoice or PO."""
    external_id: str | None = None
    item_id: str | None = None
    item_name: str | None = None
    description: str | None = None
    quantity: Decimal = Decimal(0)
    unit_price: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)
    sku: str | None = None


@dataclass
class InventoryItem:
    """
    Canonical inventory / product record.
    Maps to: inventory table, QB items.
    """
    id: str
    company_id: str
    external_id: str | None                  # QB item ID
    sku: str | None = None
    name: str = ""
    description: str | None = None
    quantity_on_hand: Decimal = Decimal(0)
    quantity_on_order: Decimal = Decimal(0)
    reorder_point: Decimal | None = None
    reorder_qty: Decimal | None = None
    unit_cost: Decimal | None = None
    unit_price: Decimal | None = None
    category: str | None = None
    location: str | None = None
    status: InventoryStatus = InventoryStatus.IN_STOCK
    last_updated: datetime | None = None
    source: DataPath = DataPath.UNKNOWN


@dataclass
class Payment:
    """Canonical payment record."""
    id: str
    company_id: str
    external_id: str | None
    customer_id: str
    customer_name: str | None = None
    amount: Decimal = Decimal(0)
    currency: str = "USD"
    payment_date: date | None = None
    payment_method: str | None = None
    reference: str | None = None
    applied_to_invoices: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    source: DataPath = DataPath.UNKNOWN


@dataclass
class PurchaseOrder:
    """Canonical purchase order."""
    id: str
    company_id: str
    external_id: str | None = None
    po_number: str | None = None
    vendor_id: str | None = None
    vendor_name: str | None = None
    status: str = "open"                     # open | received | closed | cancelled
    issue_date: date | None = None
    expected_date: date | None = None
    total: Decimal = Decimal(0)
    memo: str | None = None
    line_items: list[LineItem] = field(default_factory=list)
    created_at: datetime | None = None
    source: DataPath = DataPath.UNKNOWN


# ─── Action proposal ──────────────────────────────────────────────────────────

@dataclass
class ActionProposal:
    """
    Represents an action proposed by the AI or automation layer
    that requires policy evaluation and potentially human approval.

    The policy engine evaluates ActionProposal objects and returns
    an ActionDecision.
    """
    action_type: str                         # e.g. 'create_po', 'send_email', 'update_price'
    action_class: ActionClass
    description: str
    company_id: str
    requested_by: str                        # user_id or 'system'
    capability: str | None = None            # access router capability key
    path: DataPath | None = None             # which path would execute this
    entity_type: str | None = None
    entity_id: str | None = None
    amount: Decimal | None = None            # monetary amount if applicable
    before_snapshot: dict[str, Any] | None = None
    proposed_changes: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


@dataclass
class ActionDecision:
    """Result of the policy engine evaluating an ActionProposal."""
    proposal: ActionProposal
    effect: str                              # 'allow' | 'deny' | 'require_approval'
    rule_id: str | None = None              # which policy_rule matched
    rule_name: str | None = None
    reason: str | None = None               # human-readable explanation
    approval_roles: list[str] = field(default_factory=list)
    approval_count: int = 1


# ─── Connector entity ─────────────────────────────────────────────────────────

@dataclass
class ConnectorStatus:
    """
    Current health snapshot of a registered connector.
    Used by the web UI connector-health page and API.
    """
    id: str
    company_id: str
    connector_type: str
    connector_id: str
    version: str | None
    status: str                              # 'connected' | 'disconnected' | 'error' | 'stale'
    last_heartbeat: datetime | None
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_error: str | None
    capabilities: list[str] = field(default_factory=list)
    is_stale: bool = False
