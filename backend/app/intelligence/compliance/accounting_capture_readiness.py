"""Synthetic-first read-only plan for future QBO/QBD item capture.

DOES NOT connect to QuickBooks, create observations, write a mapping,
certify provider identity, calculate a filing deadline, or authorize filing.
In particular QBD's salesDescription is not a reliable SKU or case identity.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any


def _date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not hasattr(raw, "hour"):
        return raw
    if not isinstance(raw, str):
        raise ValueError("Price-book effective date is invalid")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("Price-book effective date is invalid") from exc


def compile_capture_mapping_readiness(
    *, company_id: str, provider: str,
    books: list[dict], bindings: list[dict], effective_date: date,
) -> dict:
    """Explain which explicit provider item-ID bindings need manual review.

    Stored mapping candidates remain UNVERIFIED even with matching labels.
    The calculation is advisory; no provider read is attempted or certified.
    """
    if provider not in ("qbo", "qbd") or not isinstance(effective_date, date):
        raise ValueError("Provider and comparison date are required")
    if (not isinstance(books, list) or not 1 <= len(books) <= 50
        or not isinstance(bindings, list) or len(bindings) > 500):
        raise ValueError("Source scope invalid")
    for row in books + bindings:
        if not isinstance(row, dict) or str(row.get("company_id")) != company_id:
            raise ValueError("Capture source tenant scope invalid")

    indexed: dict[str, dict] = {}
    for book in books:
        key = str(book.get("id") or "")
        if not key or key in indexed:
            raise ValueError("Price-book identity ambiguous")
        indexed[key] = book

    grouped: dict[str, list[dict]] = defaultdict(list)
    seen_bindings: set[str] = set()
    for row in bindings:
        key = str(row.get("id") or "")
        book_id = str(row.get("price_book_id") or "")
        if not key or key in seen_bindings or book_id not in indexed:
            raise ValueError("Unknown or duplicate capture binding ID")
        if row.get("provider") != provider:
            raise ValueError("Provider selection mismatch")
        if row.get("identity_state") != "candidate_unverified":
            raise ValueError("Source mapping must remain unverified")
        seen_bindings.add(key)
        grouped[book_id].append(row)

    items: list[dict] = []
    for book_id, book in indexed.items():
        sku = book.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("Price-book SKU missing")
        blockers: list[str] = []
        matches = grouped.get(book_id, [])
        candidate = matches[0] if len(matches) == 1 else None

        if not matches:
            blockers.append("provider_item_id_binding_missing")
        elif len(matches) > 1:
            blockers.append("multiple_provider_item_bindings_require_human_selection")
        if book.get("status") != "approved":
            blockers.append("price_book_approval_pending")
        if str(book.get("customer_ref") or "").strip():
            blockers.append("customer_specific_price_scope_not_supported")
        if not str(book.get("evidence_ref") or "").strip():
            blockers.append("price_book_document_reference_missing")
        start = _date(book.get("effective_from"))
        end = _date(book.get("effective_to"))
        if start is None or start > effective_date or (end and end < effective_date):
            blockers.append("price_book_not_effective_on_requested_date")

        resolved_provider_id = False
        basis = None
        case_units = None
        if candidate:
            resolved_provider_id = bool(str(candidate.get("source_item_id") or "").strip())
            if not resolved_provider_id:
                blockers.append("provider_item_id_missing")
            if not str(candidate.get("source_revision") or "").strip():
                blockers.append("provider_item_revision_missing")
            if not str(candidate.get("candidate_document_ref") or "").strip():
                blockers.append("item_binding_document_missing")
            if candidate.get("claimed_currency") != book.get("currency"):
                blockers.append("currency_mismatch_no_fx_inference")
            basis = candidate.get("candidate_unit_basis")
            case_units = candidate.get("candidate_units_per_case")
            if basis not in ("case", "single_unit", "unknown"):
                raise ValueError("Invalid case-pack basis")
            if basis == "single_unit":
                if (isinstance(case_units, bool) or not isinstance(case_units, int)
                    or not 1 <= case_units <= 1000):
                    raise ValueError("Invalid explicit units per case")
                blockers.append("per_unit_to_case_conversion_requires_independent_verification")
            elif case_units is not None:
                raise ValueError("Non-unit source cannot define units per case")
            if basis == "unknown":
                blockers.append("source_unit_basis_not_established")
            hint = candidate.get("source_sku_hint")
            if provider == "qbo":
                if hint and hint != sku:
                    blockers.append("qbo_sku_hint_mismatch")
                if not hint:
                    blockers.append("qbo_sku_hint_missing")
            else:
                # The existing Conductor adapter maps salesDescription into
                # InventoryItem.sku; it must NEVER act as proof of item identity.
                blockers.append("qbd_sales_description_is_not_an_authoritative_sku")
        blockers.extend((
            "provider_item_identity_not_independently_verified",
            "tenant_connection_and_realm_not_authenticated",
            "case_pack_and_price_list_scope_not_verified",
            "provider_observed_price_not_captured",
        ))
        items.append({
            "price_book_id": book_id,
            "sku": sku,
            "provider": provider,
            "candidate_binding_count": len(matches),
            "candidate_provider_item_id_present": resolved_provider_id,
            "candidate_unit_basis": basis,
            "candidate_units_per_case": case_units,
            "state": "needs_human_source_mapping",
            "blockers": blockers,
        })

    return {
        "status": "capture_prerequisites_only",
        "provider_requested": provider,
        "effective_date": effective_date.isoformat(),
        "items": items,
        "not_mapped": sum(r["candidate_binding_count"] == 0 for r in items),
        "ambiguous": sum(r["candidate_binding_count"] > 1 for r in items),
        "source_consistency": "separate_tenant_scoped_queries_not_atomic_snapshot",
        "real_provider_connection_verified": False,
        "source_items_observed_live": False,
        "provider_api_called": False,
        "item_identity_certified": False,
        "case_pack_verified": False,
        "accounting_reconciled": False,
        "safe_to_capture_live": False,
        "observation_saved": False,
        "ready_to_file": False,
        "submission_permitted": False,
        "price_published": False,
    }
