"""Read-only, non-regulatory reconciliation of price-book and compliance evidence.

This function does NOT calculate filing deadlines, perform legal clearance,
publish prices, stage provider writes, or construct an agency submission.
Source states are internal observations, not certified QBO/QBD balances.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from app.intelligence.compliance.price_posting_preview import (
    _source_date, compile_price_posting_preview,
)


def compile_source_readiness(
    *,
    rule: dict[str, Any],
    company_id: str,
    human_reviewed: bool,
    books: list[dict],
    snapshots: list[dict],
    cost_lines: list[dict],
    products: list[dict],
    licenses: list[dict],
    effective_date: date,
) -> dict:
    """Match tenant-owned source rows and return blockers, never filing approval."""
    collections = {
        "price_book": books,
        "cost_snapshots": snapshots,
        "cost_lines": cost_lines,
        "products": products,
        "licenses": licenses,
    }
    for table, rows in collections.items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {table} source")
        if any(str(row.get("company_id")) != company_id for row in rows):
            raise ValueError(f"Cross-tenant {table} source returned")

    snapshots_by_id: dict[str, dict] = {}
    for snapshot in snapshots:
        key = str(snapshot.get("id") or "")
        if not key or key in snapshots_by_id:
            raise ValueError("Cost snapshot identity ambiguous")
        snapshots_by_id[key] = snapshot

    cost_lines_by_snapshot: dict[str, list[dict]] = defaultdict(list)
    for line in cost_lines:
        key = str(line.get("cost_snapshot_id") or "")
        if not key or key not in snapshots_by_id:
            raise ValueError("Cost line belongs to an unknown snapshot")
        cost_lines_by_snapshot[key].append(line)

    products_by_sku: dict[str, list[dict]] = defaultdict(list)
    for product in products:
        sku = product.get("sku")
        if not isinstance(sku, str) or not sku.strip():
            raise ValueError("Product SKU not validated")
        products_by_sku[sku].append(product)

    details: dict[str, dict] = {}
    source_by_book_id: dict[str, dict] = {}
    seen_books: set[str] = set()

    for book in books:
        book_id = str(book.get("id") or "")
        sku = str(book.get("sku") or "")
        if not book_id or book_id in seen_books or not sku:
            raise ValueError("Price-book identity ambiguous")
        seen_books.add(book_id)
        errors: list[str] = []

        if book.get("status") != "approved":
            errors.append("price_book_approval_pending")
        if (not book.get("created_by") or not book.get("approved_by")
                or book.get("created_by") == book.get("approved_by")
                or not book.get("approved_at")):
            errors.append("independent_price_approval_not_confirmed")
        if not book.get("evidence_ref"):
            errors.append("price_book_document_reference_missing")

        product_candidates = products_by_sku.get(sku, [])
        if not product_candidates:
            errors.append("compliance_product_record_missing")
            product = None
        elif len(product_candidates) != 1:
            errors.append("compliance_product_sku_ambiguous")
            product = None
        else:
            product = product_candidates[0]

        if product:
            details[book_id] = {"product_type": product.get("product_type")}
            if product.get("product_type") not in ("wine", "spirits", "beer", "malt"):
                errors.append("product_classification_not_assessed")
        else:
            details[book_id] = {}

        # A recorded license is not independently verified or a filing authority.
        candidates = [
            lic for lic in licenses
            if lic.get("state_code") == rule.get("region_code")
            and lic.get("status") == "active"
            and product
            and isinstance(lic.get("product_types"), list)
            and product.get("product_type") in lic["product_types"]
            and bool(str(lic.get("license_number") or "").strip())
        ]
        if not candidates:
            errors.append("matching_internal_license_record_missing")
        if len(candidates) > 1:
            errors.append("multiple_license_candidates_require_human_selection")

        snapshot_id = str(book.get("cost_snapshot_id") or "")
        snapshot = snapshots_by_id.get(snapshot_id) if snapshot_id else None
        if snapshot is None:
            errors.append("cost_snapshot_missing")
        else:
            if snapshot.get("sku") != sku:
                errors.append("cost_snapshot_sku_mismatch")
            if snapshot.get("currency") != book.get("currency"):
                errors.append("cost_snapshot_currency_mismatch")
            if snapshot.get("evidence_status") != "reconciled":
                errors.append("cost_snapshot_not_reconciled")
            snapshot_date = _source_date(snapshot.get("cost_as_of"))
            if snapshot_date is None or snapshot_date > effective_date:
                errors.append("cost_snapshot_date_invalid_or_future")

            lines = cost_lines_by_snapshot.get(snapshot_id, [])
            if not lines:
                errors.append("cost_document_line_evidence_missing")
            elif (
                any(line.get("observation_status") != "verified"
                    or not line.get("source_document_ref")
                    or not line.get("source_revision")
                    for line in lines)
            ):
                errors.append("cost_document_lines_not_verified")
            if len({str(line.get("expense_line_ref")) for line in lines}) != len(lines):
                errors.append("cost_expense_line_identity_ambiguous")

        source_by_book_id[book_id] = {
            "price_book_id": book_id,
            "sku": sku,
            "cost_snapshot_id": snapshot_id or None,
            "cost_basis_recorded_as_reconciled": bool(
                snapshot
                and snapshot.get("evidence_status") == "reconciled"
                and snapshot.get("sku") == sku
                and snapshot.get("currency") == book.get("currency")
                and (_source_date(snapshot.get("cost_as_of")) is not None)
                and _source_date(snapshot.get("cost_as_of")) <= effective_date
                and cost_lines_by_snapshot.get(snapshot_id)
                and all(
                    line.get("observation_status") == "verified"
                    and bool(line.get("source_document_ref"))
                    and bool(line.get("source_revision"))
                    for line in cost_lines_by_snapshot[snapshot_id]
                )
            ),
            "matching_license_record_count": len(candidates),
            "product_type_from_internal_registry": (
                product.get("product_type") if product else None
            ),
            "source_blockers": errors,
            "independent_qbo_qbd_reconciliation_confirmed": False,
            "license_authority_verified_by_regulator": False,
            "warehouse_cost_coverage_certified": False,
        }

    preview = compile_price_posting_preview(
        rule=rule,
        verified=human_reviewed,
        price_book_rows=books,
        detail_by_book_id=details,
        requested_effective=effective_date,
    )

    # Preserve all blockers, even when a product has a clean INTERNAL match.
    blockers = list(preview["blockers"])
    for book_id in sorted(source_by_book_id):
        blockers.extend(
            f"{book_id}:{error}" for error in source_by_book_id[book_id]["source_blockers"]
        )
    blockers.extend([
        "independent_qbo_qbd_reconciliation_not_verified",
        "licensed_filer_and_regulatory_scope_require_human_confirmation",
        "warehouse_cost_coverage_not_independently_verified",
        "product_label_and_brand_registrations_not_independently_verified",
        "price_posting_requirements_may_change_after_source_review",
    ])
    preview.update({
        "preliminary_state": "blocked_for_review",
        "blockers": blockers,
        "source_evidence": [source_by_book_id[str(book["id"])] for book in books],
        "internal_source_reconciliation": "checked_with_unresolved_evidence",
        "source_consistency": "separate_tenant_scoped_queries_not_atomic_snapshot",
        "source_detail_provenance": "internal_registry_only_no_filing_field_inference",
        "qbo_qbd_live_data_accessed": False,
        "independent_accounting_reconciled": False,
        "price_book_modified": False,
        "ready_to_file": False,
        "submission_permitted": False,
        "agency_template_generated": False,
    })
    return preview
