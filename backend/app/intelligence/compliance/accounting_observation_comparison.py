"""Deterministic *numeric* QB Online/Desktop observation comparison.

Sources are tenant-owned stored observations, NOT independently authenticated
connector data. No adapter call, price modification, jurisdiction clearance or
agency submission occurs here. A numerical match is never financial approval.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal(value: Any, *, label: str) -> Decimal:
    # Accept decimal text/int and safe roundtrippable DB numeric responses.
    # No float arithmetic is performed, and non-finite values are forbidden.
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label} missing")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{label} invalid") from exc
    if not number.is_finite() or number < 0:
        raise ValueError(f"{label} invalid")
    return number


def _observed_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Observation timestamp missing")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Observation timestamp invalid") from exc
    if instant.tzinfo is None:
        raise ValueError("Observation timestamp must contain timezone")
    return instant.astimezone(timezone.utc)


def compare_accounting_observations(
    *, company_id: str, books: list[dict], observations: list[dict],
    evaluated_at: datetime,
) -> dict:
    """Fail closed on bad evidence, expose only unverified numerical comparisons.

    Each selected price-book revision must have exactly one matching internal
    accounting observation, or is treated as not comparable. Multiple prices
    are never silently reduced to the newest; that requires human resolution.
    """
    if evaluated_at.tzinfo is None:
        raise ValueError("Comparison time must be timezone-aware")
    when = evaluated_at.astimezone(timezone.utc)
    if not isinstance(books, list) or not isinstance(observations, list):
        raise ValueError("Source collections invalid")
    if not 1 <= len(books) <= 50:
        raise ValueError("Invalid selected price-book scope")
    if len(observations) > 500:
        raise ValueError("Accounting source scope truncated")
    for row in books + observations:
        if not isinstance(row, dict) or str(row.get("company_id")) != company_id:
            raise ValueError("Accounting source tenant scope invalid")

    by_book: dict[str, list[dict]] = defaultdict(list)
    seen_obs: set[str] = set()
    for obs in observations:
        oid = str(obs.get("id") or "")
        book_id = str(obs.get("price_book_id") or "")
        if not oid or oid in seen_obs or not book_id:
            raise ValueError("Accounting observation ID ambiguous")
        seen_obs.add(oid)
        if (obs.get("observation_state") != "internally_recorded_unverified"
            or obs.get("source_origin") not in (
                "synthetic_test", "connector_snapshot_unverified"
            )):
            raise ValueError("Unrecognized accounting observation provenance")
        by_book[book_id].append(obs)

    selected = {str(b.get("id") or "") for b in books}
    if len(selected) != len(books) or "" in selected:
        raise ValueError("Price-book source identity ambiguous")
    if any(book_id not in selected for book_id in by_book):
        raise ValueError("Orphan accounting observation returned")

    checks: list[dict] = []
    for book in books:
        book_id = str(book["id"])
        sku = str(book.get("sku") or "")
        if not sku:
            raise ValueError("Price book SKU invalid")
        candidates = by_book.get(book_id, [])
        blockers: list[str] = []
        result: dict[str, Any] = {
            "price_book_id": book_id,
            "sku": sku,
            "accounting_observation_id": None,
            "provider": None,
            "observed_amount_per_case": None,
            "book_amount_per_case": None,
            "numeric_comparison": "not_comparable",
            "blockers": blockers,
        }

        book_amount = _decimal(book.get("list_price_per_case"), label="Price-book amount")
        if book_amount == 0:
            blockers.append("price_book_zero_amount")
        result["book_amount_per_case"] = str(book_amount)

        if not candidates:
            blockers.append("accounting_observation_missing")
        elif len(candidates) > 1:
            blockers.append("ambiguous_accounting_observations_require_selection")
        else:
            obs = candidates[0]
            result["accounting_observation_id"] = str(obs["id"])
            result["provider"] = obs.get("provider")
            if obs.get("provider") not in ("qbo", "qbd"):
                blockers.append("accounting_provider_unknown")
            if obs.get("sku") != sku:
                blockers.append("accounting_sku_mismatch")
            if obs.get("currency") != book.get("currency"):
                blockers.append("currency_mismatch_no_fx_inference")
            if not str(obs.get("source_item_id") or "").strip():
                blockers.append("accounting_item_identity_missing")
            if not str(obs.get("source_revision") or "").strip():
                blockers.append("accounting_revision_missing")
            if not str(obs.get("source_document_ref") or "").strip():
                blockers.append("accounting_document_reference_missing")
            if obs.get("source_origin") == "synthetic_test":
                blockers.append("synthetic_test_source_only")

            observed_at = _observed_time(obs.get("observed_at"))
            if observed_at > when:
                blockers.append("accounting_observation_from_future")

            basis = obs.get("unit_basis")
            if basis not in ("case", "single_unit", "unknown"):
                raise ValueError("Unrecognized accounting unit basis")
            unit_price = _decimal(obs.get("observed_unit_price"), label="Observed amount")
            observed_per_case: Decimal | None = None
            if basis == "case":
                if obs.get("units_per_case") is not None:
                    raise ValueError("Case-price source has incompatible unit count")
                observed_per_case = unit_price
            elif basis == "single_unit":
                count = obs.get("units_per_case")
                if (isinstance(count, bool) or not isinstance(count, int)
                        or not 1 <= count <= 1000):
                    raise ValueError("Explicit accounting unit-count evidence invalid")
                observed_per_case = unit_price * Decimal(count)
                blockers.append("case_pack_conversion_not_independently_verified")
            else:
                blockers.append("accounting_unit_basis_unknown")
                if obs.get("units_per_case") is not None:
                    raise ValueError("Unknown unit basis cannot have case count")

            # Any source/currency/SKU identity mismatch prevents comparisons.
            blocking_identity = {
                "accounting_provider_unknown",
                "accounting_sku_mismatch",
                "currency_mismatch_no_fx_inference",
                "accounting_item_identity_missing",
                "accounting_revision_missing",
                "accounting_document_reference_missing",
                "accounting_observation_from_future",
            }
            if observed_per_case is not None and not set(blockers).intersection(blocking_identity):
                result["observed_amount_per_case"] = str(observed_per_case)
                result["numeric_comparison"] = (
                    "numeric_match_unverified"
                    if observed_per_case == book_amount else "numeric_difference"
                )
                if observed_per_case != book_amount:
                    blockers.append("accounting_amount_difference")
                else:
                    blockers.append("numerical_match_does_not_certify_accounting")
        if book.get("status") != "approved":
            blockers.append("price_book_not_approved")
        if str(book.get("customer_ref") or "").strip():
            blockers.append("customer_specific_scope_not_equivalent_to_global_item_price")
            result["numeric_comparison"] = "not_comparable"
        blockers.extend((
            "accounting_connector_provenance_not_independently_authenticated",
            "accounting_list_price_scope_and_effective_period_not_certified",
        ))
        checks.append(result)

    return {
        "status": "internal_numeric_comparison_only",
        "checks": checks,
        "numeric_matches_unverified": sum(
            r["numeric_comparison"] == "numeric_match_unverified" for r in checks
        ),
        "numeric_differences": sum(
            r["numeric_comparison"] == "numeric_difference" for r in checks
        ),
        "uncomparable": sum(
            r["numeric_comparison"] == "not_comparable" for r in checks
        ),
        "source_consistency": "separate_tenant_scoped_queries_not_atomic_snapshot",
        "independent_qbo_qbd_reconciliation_confirmed": False,
        "warehouse_cost_coverage_verified": False,
        "regulatory_requirements_verified": False,
        "ready_to_file": False,
        "submission_permitted": False,
        "agency_template_generated": False,
        "qbo_qbd_live_data_accessed": False,
        "live_price_changed": False,
    }
