"""Offline-only QB inventory fixture validation for future price-posting capture.

Takes ALREADY NORMALIZED synthetic InventoryItem instances. It has no
database, HTTP, QuickBooks adapter or credential access, and cannot save a
financial observation. Passing a fixture does NOT verify a live connector,
source revision, source currency, regulatory list price or accounting balance.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.connectors.base import InventoryItem
from app.intelligence.compliance.accounting_capture_readiness import _date


def _money(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("Missing price")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid price") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Invalid or non-finite price")
    return number


def preview_synthetic_qb_inventory_snapshot(
    *,
    company_id: str,
    provider: str,
    expected_connection_ref: str,
    fixture_company_id: str,
    fixture_connection_ref: str,
    fixture_observed_at: datetime,
    evaluated_at: datetime,
    complete_fixture: bool,
    effective_date: date,
    books: list[dict],
    bindings: list[dict],
    items: list[InventoryItem],
) -> dict:
    """Match by provider item ID only and compare numeric case-price candidates.

    A matching case price is still *unverified*. This is not an ingestion
    adapter or a live preview endpoint; callers may only pass offline fixtures.
    """
    if provider not in ("qbo", "qbd"):
        raise ValueError("Unsupported QuickBooks fixture provider")
    if not company_id or fixture_company_id != company_id:
        raise ValueError("Fixture tenant identity mismatch")
    if (not isinstance(expected_connection_ref, str)
            or not expected_connection_ref.strip()
            or fixture_connection_ref != expected_connection_ref):
        raise ValueError("Fixture connection identity mismatch")
    if not isinstance(complete_fixture, bool) or complete_fixture is not True:
        raise ValueError("Incomplete fixture is not evidence of absence")
    if not isinstance(fixture_observed_at, datetime) or not isinstance(evaluated_at, datetime):
        raise ValueError("Fixture timestamps missing")
    if fixture_observed_at.tzinfo is None or evaluated_at.tzinfo is None:
        raise ValueError("Fixture timestamps need timezones")
    if fixture_observed_at.astimezone(timezone.utc) > evaluated_at.astimezone(timezone.utc):
        raise ValueError("Fixture timestamp in the future")
    if not isinstance(effective_date, date) or isinstance(effective_date, datetime):
        raise ValueError("Effective date invalid")
    if (not isinstance(books, list) or not 1 <= len(books) <= 50
            or not isinstance(bindings, list) or len(bindings) > 500
            or not isinstance(items, list) or len(items) > 1000):
        raise ValueError("Unbounded or missing fixture source")
    if any(
        not isinstance(row, dict) or str(row.get("company_id")) != company_id
        for row in books + bindings
    ):
        raise ValueError("Cross-tenant fixture evidence")

    by_book: dict[str, dict] = {}
    for book in books:
        key = str(book.get("id") or "")
        if not key or key in by_book:
            raise ValueError("Price-book identity ambiguous")
        by_book[key] = book

    selected_bindings: dict[str, list[dict]] = defaultdict(list)
    seen_binding_ids: set[str] = set()
    for binding in bindings:
        binding_id = str(binding.get("id") or "")
        book_id = str(binding.get("price_book_id") or "")
        if (not binding_id or binding_id in seen_binding_ids
                or book_id not in by_book or binding.get("provider") != provider
                or binding.get("identity_state") != "candidate_unverified"):
            raise ValueError("Binding identity or provider ambiguous")
        seen_binding_ids.add(binding_id)
        selected_bindings[book_id].append(binding)

    by_source_id: dict[str, InventoryItem] = {}
    for item in items:
        if not isinstance(item, InventoryItem):
            raise ValueError("Unexpected normalized provider inventory type")
        source_id = item.qb_id
        if (not isinstance(source_id, str) or not source_id.strip()
                or source_id in by_source_id
                or not isinstance(item.id, str) or item.id != source_id):
            raise ValueError("Provider item ID missing, conflicting or duplicated")
        if not isinstance(item.unit_price, Decimal):
            raise ValueError("Normalized provider price must use Decimal")
        # Detect malformed numeric data even when an item is not selected,
        # so one corrupt fixture never falsely implies complete coverage.
        _money(item.unit_price)
        by_source_id[source_id] = item

    results: list[dict] = []
    for book_id, book in by_book.items():
        sku = book.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("Price-book SKU missing")
        list_price = _money(book.get("list_price_per_case"))
        row = {
            "price_book_id": book_id,
            "sku": sku,
            "candidate_binding_count": len(selected_bindings.get(book_id, [])),
            "exact_provider_item_id_found_in_fixture": False,
            "observed_case_amount": None,
            "book_case_amount": str(list_price),
            "numeric_comparison": "not_comparable",
            "blockers": [],
        }
        blockers: list[str] = row["blockers"]
        if book.get("status") != "approved":
            blockers.append("price_book_approval_pending")
        if str(book.get("customer_ref") or "").strip():
            blockers.append("customer_specific_price_scope_not_confirmed")
        if not str(book.get("evidence_ref") or "").strip():
            blockers.append("price_book_approval_document_missing")
        from_date = _date(book.get("effective_from"))
        until_date = _date(book.get("effective_to"))
        if from_date is None or from_date > effective_date or (
            until_date is not None and until_date < effective_date
        ):
            blockers.append("price_book_not_effective_for_fixture_date")
        candidates = selected_bindings.get(book_id, [])
        if not candidates:
            blockers.append("explicit_provider_item_binding_missing")
        elif len(candidates) > 1:
            blockers.append("ambiguous_provider_item_binding")
        else:
            candidate = candidates[0]
            provider_id = candidate.get("source_item_id")
            if not isinstance(provider_id, str) or not provider_id.strip():
                blockers.append("explicit_provider_item_id_missing")
                provider_id = None
            if not str(candidate.get("source_revision") or "").strip():
                blockers.append("candidate_source_revision_missing")
            if not str(candidate.get("candidate_document_ref") or "").strip():
                blockers.append("candidate_item_mapping_document_missing")
            if candidate.get("claimed_currency") != book.get("currency"):
                blockers.append("candidate_currency_mismatch_no_fx_inference")
            mapped = by_source_id.get(provider_id) if provider_id else None
            if mapped is None:
                blockers.append("bound_item_not_in_complete_synthetic_fixture")
            else:
                row["exact_provider_item_id_found_in_fixture"] = True
                if provider == "qbo":
                    # QBO Sku is a helpful hint but cannot replace exact ID.
                    hint = candidate.get("source_sku_hint")
                    if (not isinstance(hint, str) or not hint.strip()
                            or hint != sku or mapped.sku != sku):
                        blockers.append("qbo_sku_hint_or_source_mismatch")
                else:
                    # The existing QBD adapter uses salesDescription as
                    # InventoryItem.sku. It is NOT an identity key.
                    blockers.append("qbd_sales_description_cannot_prove_sku")
                unit_basis = candidate.get("candidate_unit_basis")
                pack_count = candidate.get("candidate_units_per_case")
                observed_per_case: Decimal | None = None
                unit_price = _money(mapped.unit_price)
                if unit_price == 0:
                    blockers.append("provider_price_zero_or_unavailable")
                elif unit_basis == "case":
                    if pack_count is not None:
                        raise ValueError("Case-price candidate cannot have pack factor")
                    observed_per_case = unit_price
                elif unit_basis == "single_unit":
                    if (isinstance(pack_count, bool) or not isinstance(pack_count, int)
                            or not 1 <= pack_count <= 1000):
                        raise ValueError("Single-unit candidate needs explicit case factor")
                    observed_per_case = unit_price * Decimal(pack_count)
                    blockers.append("candidate_case_pack_not_independently_verified")
                elif unit_basis == "unknown":
                    if pack_count is not None:
                        raise ValueError("Unknown unit basis cannot have pack factor")
                    blockers.append("provider_unit_basis_unknown")
                else:
                    raise ValueError("Invalid unit basis")
                if (observed_per_case is not None
                        and not set(blockers).intersection({
                            "qbo_sku_hint_or_source_mismatch",
                            "candidate_currency_mismatch_no_fx_inference",
                            "candidate_source_revision_missing",
                            "candidate_item_mapping_document_missing",
                            "price_book_approval_pending",
                            "price_book_not_effective_for_fixture_date",
                            "customer_specific_price_scope_not_confirmed",
                            "price_book_approval_document_missing",
                        })):
                    row["observed_case_amount"] = str(observed_per_case)
                    row["numeric_comparison"] = (
                        "numeric_match_unverified"
                        if observed_per_case == list_price else "numeric_difference"
                    )
                    if observed_per_case != list_price:
                        blockers.append("numeric_case_price_difference")
                    else:
                        blockers.append("numeric_match_does_not_establish_accounting_truth")
        blockers.extend((
            "synthetic_fixture_only_no_live_provider_access",
            "provider_connection_not_independently_authenticated",
            "provider_item_revision_not_in_normalized_inventory_contract",
            "currency_not_verified_in_provider_item_contract",
            "list_price_scope_not_verified",
            "freight_and_warehouse_costs_not_reconciled",
        ))
        results.append(row)

    return {
        "status": "synthetic_inventory_fixture_preview_only",
        "provider": provider,
        "lines": results,
        "numeric_matches_unverified": sum(
            row["numeric_comparison"] == "numeric_match_unverified" for row in results
        ),
        "numeric_differences": sum(
            row["numeric_comparison"] == "numeric_difference" for row in results
        ),
        "not_comparable": sum(
            row["numeric_comparison"] == "not_comparable" for row in results
        ),
        "live_provider_called": False,
        "live_connection_authenticated": False,
        "observation_saved": False,
        "price_written": False,
        "independent_accounting_reconciled": False,
        "warehouse_costs_verified": False,
        "safe_to_capture_live": False,
        "ready_to_file": False,
        "submission_permitted": False,
    }
