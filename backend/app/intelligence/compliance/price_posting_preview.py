"""Conservative, jurisdiction-configured price posting preview compiler.

No filing, export or pricing mutation occurs here. Legal applicability, official
templates and filing dates require separately recorded HUMAN review of the
current rule version and the tenant's license/market facts.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


def _money(value: Any) -> str | None:
    """Preserve the source's precision; never silently round a posted price."""
    if isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -6:
        return None
    return format(amount, "f")


def _source_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _detail_present(field: str, value: Any) -> bool:
    if field == "units_per_case":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10000
    if field == "counties":
        return (isinstance(value, list) and 0 < len(value) <= 67
                and all(isinstance(c, str) and 0 < len(c.strip()) <= 80 for c in value)
                and len({c.strip().casefold() for c in value}) == len(value))
    if field == "cash_price_after_posting":
        return _money(value) is not None
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 500


def compile_price_posting_preview(
    *,
    rule: dict,
    verified: bool,
    price_book_rows: list[dict],
    detail_by_book_id: dict[str, dict],
    requested_effective: date,
) -> dict:
    """Validate draft pricing against *candidate* fields, not legal sufficiency."""
    blockers: list[str] = []
    lines: list[dict] = []

    if not verified:
        blockers.append("human_rule_verification_required")
    if rule.get("filing_deadline_rule") is None:
        blockers.append("filing_deadline_unverified")
    if not rule.get("official_template_url"):
        blockers.append("current_official_template_unverified")

    required_details = rule.get("required_details")
    channels = rule.get("allowed_channels")
    product_types = rule.get("product_types")
    if not all(isinstance(a, list) and a and all(isinstance(v, str) for v in a)
               for a in (required_details, channels, product_types)):
        raise ValueError("Price-posting rule version is incomplete")

    for row in price_book_rows:
        line_id = str(row.get("id") or "")
        sku = row.get("sku")
        if not line_id or not isinstance(sku, str) or not sku.strip():
            raise ValueError("Price-book identity is incomplete")
        details = detail_by_book_id.get(line_id, {})
        errors: list[str] = []
        if not isinstance(details, dict):
            raise ValueError("Product metadata must be an object")

        if row.get("market_code") != rule.get("region_code"):
            errors.append("market_mismatch")
        if row.get("channel") not in channels:
            errors.append("sales_channel_applicability_unverified")
        if row.get("currency") != "USD":
            errors.append("currency_unsupported")
        if row.get("status") != "approved":
            errors.append("price_book_not_approved")
        if not row.get("cost_snapshot_id") or not row.get("approved_by") or not row.get("approved_at"):
            errors.append("cost_and_approval_evidence_incomplete")
        if not row.get("evidence_ref"):
            errors.append("pricing_evidence_ref_missing")
        if row.get("customer_ref"):
            errors.append("customer_specific_pricing_requires_manual_trade_practice_review")

        value = _money(row.get("list_price_per_case"))
        if value is None:
            errors.append("source_price_invalid")
        effective_from = _source_date(row.get("effective_from"))
        effective_to = _source_date(row.get("effective_to")) if row.get("effective_to") else None
        if not effective_from or effective_from > requested_effective or (
            effective_to and effective_to < requested_effective
        ):
            errors.append("price_book_effective_period_mismatch")

        for field in required_details:
            if not _detail_present(field, details.get(field)):
                errors.append("missing_or_invalid_" + field)
        if _detail_present("product_type", details.get("product_type")):
            if details["product_type"] not in product_types:
                errors.append("product_type_outside_candidate_scope")

        # No direct customer, cost or accounting details are returned or stored.
        lines.append({
            "price_book_id": line_id,
            "sku": sku,
            "package_label": row.get("package_label"),
            "list_price_per_case": value,
            "currency": row.get("currency"),
            "effective_from": str(row.get("effective_from") or ""),
            "price_revision": row.get("revision"),
            "draft_product_details": {k: details.get(k) for k in required_details},
            "blockers": errors,
        })
        blockers.extend(f"{line_id}:{e}" for e in errors)

    if not lines:
        blockers.append("no_price_book_lines")
    # This endpoint deliberately NEVER enables regulatory submission, even if
    # all draft fields pass. Official agency templates are not generated here.
    return {
        "program_key": rule["program_key"],
        "rule_version_id": str(rule["id"]),
        "rule_version": rule["version"],
        "country_code": rule["country_code"],
        "region_code": rule["region_code"],
        "requested_effective_date": requested_effective.isoformat(),
        "human_rule_review_recorded": verified,
        "preliminary_state": "internal_review_possible" if not blockers else "blocked_for_review",
        "lines": lines,
        "blockers": blockers,
        "disclaimer": "PREVIEW ONLY: candidate requirements are not a legal compliance determination.",
        "submission_permitted": False,
        "agency_template_generated": False,
        "live_price_changed": False,
        "quickbooks_write_performed": False,
    }
