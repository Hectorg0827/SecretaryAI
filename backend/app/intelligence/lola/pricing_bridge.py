"""Read-only pricing evidence bridge for Lola.

This module never assumes a Price Book entry was published to QuickBooks.
The three source reads are not a transactionally consistent database snapshot.
Prices, margins and payment instructions are never written or approved here.
"""
from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.intelligence.pricing.waterfall import REQUIRED_COMPONENTS, WAREHOUSE_COMPONENTS, KNOWN_COST_COMPONENTS

log = logging.getLogger(__name__)
MAX_COST_LINES = 200
MAX_PRICE_BOOK_ROWS = 100
MAX_LIST_ROWS = 50


class PricingEvidenceUnavailable(RuntimeError):
    """Cost evidence is missing, inconsistent, ambiguous or unavailable."""


class PricingSnapshotNotFound(LookupError):
    """Record not found inside the current tenant."""


def _rows(query) -> list[dict]:
    try:
        result = query.execute()
        rows = getattr(result, "data", None)
    except Exception as exc:
        log.warning("Lola pricing evidence source query failed (%s)", type(exc).__name__)
        raise PricingEvidenceUnavailable("Pricing source unavailable") from None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PricingEvidenceUnavailable("Pricing result malformed")
    return rows


def _required_identity(value, name: str, *, max_length: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise PricingEvidenceUnavailable(f"Invalid {name}")
    return value


def _exact_amount(value, name: str, *, positive: bool = False) -> Decimal:
    # PostgREST numeric values must be selected ::text to avoid float rounding.
    if not isinstance(value, str) or len(value) > 40:
        raise PricingEvidenceUnavailable(f"{name} requires an exact numeric string")
    try:
        amount = Decimal(value)
    except (ValueError, InvalidOperation) as exc:
        raise PricingEvidenceUnavailable(f"Invalid {name}") from exc
    if not amount.is_finite() or (amount <= 0 if positive else amount < 0) or amount > Decimal("1000000000000"):
        raise PricingEvidenceUnavailable(f"{name} outside supported range")
    return amount


def _check_tenant(rows: list[dict], company_id: str, field: str = "company_id") -> None:
    if any(row.get(field) != company_id for row in rows):
        raise PricingEvidenceUnavailable("Tenant isolation failure")


def review_cost_snapshot(*, company_id: str, snapshot_id: UUID, db) -> dict:
    """Evidence-quality assessment from three tenant-scoped, bounded queries."""
    _required_identity(company_id, "company_id", max_length=120)
    id_str = str(snapshot_id)
    snapshot_rows = _rows(
        db.table("pricing_cost_snapshots")
          .select("id,company_id,sku,shipment_ref,currency,accepted_cases::text,cost_as_of,source_revision,evidence_status")
          .eq("company_id", company_id).eq("id", id_str).limit(2)
    )
    _check_tenant(snapshot_rows, company_id)
    if not snapshot_rows:
        raise PricingSnapshotNotFound("Cost snapshot not found")
    if len(snapshot_rows) != 1 or str(snapshot_rows[0].get("id")) != id_str:
        raise PricingEvidenceUnavailable("Ambiguous cost snapshot identity")
    snapshot = snapshot_rows[0]
    sku = _required_identity(snapshot.get("sku"), "sku", max_length=120)
    shipment = _required_identity(snapshot.get("shipment_ref"), "shipment_ref", max_length=120)
    revision = _required_identity(snapshot.get("source_revision"), "source_revision", max_length=120)
    currency = snapshot.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isupper() or not currency.isalpha():
        raise PricingEvidenceUnavailable("Currency malformed")
    accepted_cases = _exact_amount(snapshot.get("accepted_cases"), "accepted_cases", positive=True)
    if snapshot.get("evidence_status") not in ("provisional", "reconciled"):
        raise PricingEvidenceUnavailable("Unknown snapshot evidence status")
    _required_identity(snapshot.get("cost_as_of"), "cost_as_of", max_length=20)

    cost_lines = _rows(
        db.table("pricing_cost_lines")
          .select("company_id,cost_snapshot_id,expense_line_ref,component,cost_per_case::text,source_document_ref,source_revision,allocation_basis,observation_status")
          .eq("company_id", company_id).eq("cost_snapshot_id", id_str).limit(MAX_COST_LINES + 1)
    )
    _check_tenant(cost_lines, company_id)
    if len(cost_lines) > MAX_COST_LINES:
        raise PricingEvidenceUnavailable("Cost line scope exceeds supported page")

    price_book = _rows(
        db.table("pricing_price_book")
          .select("id,company_id,sku,cost_snapshot_id,currency,status,revision,market_code,channel,effective_from,effective_to,list_price_per_case::text,evidence_ref,approved_at")
          .eq("company_id", company_id).eq("cost_snapshot_id", id_str).limit(MAX_PRICE_BOOK_ROWS + 1)
    )
    _check_tenant(price_book, company_id)
    if len(price_book) > MAX_PRICE_BOOK_ROWS:
        raise PricingEvidenceUnavailable("Price-book scope exceeds supported page")

    total = Decimal("0")
    components: set[str] = set()
    line_refs: set[str] = set()
    unverified_count = 0
    for line in cost_lines:
        if str(line.get("cost_snapshot_id")) != id_str:
            raise PricingEvidenceUnavailable("Cost line snapshot mismatch")
        ref = _required_identity(line.get("expense_line_ref"), "expense line", max_length=160)
        if ref in line_refs:
            raise PricingEvidenceUnavailable("Duplicate invoice charge")
        line_refs.add(ref)
        component = _required_identity(line.get("component"), "component", max_length=60)
        if component not in KNOWN_COST_COMPONENTS:
            raise PricingEvidenceUnavailable("Unrecognized cost component")
        components.add(component)
        amount = _exact_amount(line.get("cost_per_case"), "cost_per_case")
        total += amount
        if total > Decimal("1000000000000"):
            raise PricingEvidenceUnavailable("Total cost outside supported range")
        if (line.get("observation_status") != "verified"
            or not line.get("source_document_ref")
            or not line.get("source_revision")):
            unverified_count += 1
        if line.get("observation_status") not in ("verified", "provisional"):
            raise PricingEvidenceUnavailable("Unknown cost observation status")

    missing_inbound = sorted(REQUIRED_COMPONENTS - components)
    missing_warehouse = sorted(WAREHOUSE_COMPONENTS - components)
    documented = (
        bool(cost_lines)
        and not unverified_count
        and snapshot["evidence_status"] == "reconciled"
        and not missing_inbound
        and not missing_warehouse
    )
    books: list[dict] = []
    for book in price_book:
        if (str(book.get("cost_snapshot_id")) != id_str or book.get("sku") != sku
            or book.get("currency") != currency):
            raise PricingEvidenceUnavailable("Mismatched price-book source")
        price = _exact_amount(book.get("list_price_per_case"), "list_price_per_case", positive=True)
        status = book.get("status")
        if status not in ("draft", "pending_approval", "approved", "retired"):
            raise PricingEvidenceUnavailable("Unrecognized price-book state")
        books.append({
            "price_book_id": _required_identity(str(book.get("id") or ""), "price book id"),
            "market_code": _required_identity(book.get("market_code"), "market_code", max_length=60),
            "channel": _required_identity(book.get("channel"), "channel", max_length=60),
            "recorded_status": status,
            "list_price_per_case": str(price),
            "source_revision": book.get("revision"),
            "source_evidence_ref": book.get("evidence_ref") or None,
            "publication_verified": False,
            "effective_from": book.get("effective_from"),
        })

    reasons: list[str] = []
    if not cost_lines:
        reasons.append("no_cost_lines")
    if snapshot["evidence_status"] != "reconciled":
        reasons.append("cost_snapshot_provisional")
    if unverified_count:
        reasons.append("unverified_cost_lines")
    if missing_inbound:
        reasons.append("missing_inbound_cost_categories")
    if missing_warehouse:
        reasons.append("missing_warehouse_cost_categories")
    if not books:
        reasons.append("no_linked_price_book")
    elif not any(b["recorded_status"] == "approved" for b in books):
        reasons.append("no_recorded_approved_price")
    # Even a fully documented snapshot is NOT an independent accounting reconciliation:
    # no QuickBooks bill, invoice, discount, rebate or customer price check occurred.
    return {
        "company_id": company_id,
        "cost_snapshot_id": id_str,
        "sku": sku,
        "shipment_ref": shipment,
        "source_revision": revision,
        "source_cost_as_of": snapshot["cost_as_of"],
        "currency": currency,
        "accepted_cases": str(accepted_cases),
        "source_evidence_status": snapshot["evidence_status"],
        "recorded_cost_per_case": str(total) if cost_lines else None,
        "recorded_cost_kind": "partial_or_unverified_until_independent_reconciliation",
        "component_count": len(components),
        "cost_line_count": len(cost_lines),
        "unverified_cost_line_count": unverified_count,
        "missing_inbound_components": missing_inbound,
        "missing_warehouse_components": missing_warehouse,
        "source_documentation_complete": documented,
        "price_book_entries": books,
        "issues_requiring_review": reasons,
        "review_state": "needs_evidence" if reasons else "ready_for_independent_reconciliation",
        "accounting_reconciled": False,
        "net_revenue_verified": False,
        "gross_margin_calculated": False,
        "price_publication_verified": False,
        "read_consistency": "independent_tenant_scoped_queries_not_atomic",
        "human_approval_required": True,
        "actions_staged": False,
        "external_writes_executed": False,
        "source": "existing_pricing_snapshots_not_live_quickbooks",
    }


def list_cost_snapshot_candidates(*, company_id: str, db, limit: int = 5) -> dict:
    """Small discovery index, not a certification of missing or complete costs."""
    _required_identity(company_id, "company_id", max_length=120)
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("Unsupported candidate limit")
    rows = _rows(
        db.table("pricing_cost_snapshots")
          .select("id,company_id,sku,shipment_ref,currency,cost_as_of,evidence_status,source_revision")
          .eq("company_id", company_id)
          .order("cost_as_of", desc=True)
          .limit(limit + 1)
    )
    _check_tenant(rows, company_id)
    entries = []
    for row in rows[:limit]:
        if row.get("evidence_status") not in ("reconciled", "provisional"):
            raise PricingEvidenceUnavailable("Unrecognized snapshot state")
        entries.append({
            "cost_snapshot_id": _required_identity(str(row.get("id") or ""), "snapshot id"),
            "sku": _required_identity(row.get("sku"), "sku", max_length=120),
            "shipment_ref": _required_identity(row.get("shipment_ref"), "shipment_ref", max_length=120),
            "source_revision": _required_identity(row.get("source_revision"), "source_revision"),
            "source_cost_as_of": _required_identity(row.get("cost_as_of"), "cost_as_of", max_length=20),
            "source_evidence_status": row["evidence_status"],
        })
    return {
        "state": "snapshot_candidates" if entries else "no_pricing_snapshots_recorded",
        "scope": "existing_staged_pricing_snapshots_only",
        "items": entries,
        "has_more": len(rows) > limit,
        "price_changes_allowed": False,
        "accounting_reconciled": False,
        "source_certification": False,
    }
