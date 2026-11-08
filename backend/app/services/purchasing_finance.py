"""Conservative PO valuation and read-only QuickBooks Bill checks.

Financial inputs are *provisional assertions*, not supplier invoices or booked
accounting entries. This module never posts inventory, bills, or journal entries.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


CENT = Decimal("0.01")
SIX = Decimal("0.000001")


def money(raw: Any, *, allow_zero: bool = True) -> Decimal:
    """Parse exact, bounded finance numbers; refuse floats and NaN/Infinity."""
    if isinstance(raw, (float, bool)) or raw is None:
        raise ValueError("Finance amount must be an exact decimal string")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid finance decimal") from exc
    if (not value.is_finite() or value < 0 or (not allow_zero and value == 0)
            or value > Decimal("999999999999.999999") or value != value.quantize(SIX)):
        raise ValueError("Finance decimal outside supported bounds")
    return value


def _as_case_quantity(raw: Any) -> Decimal:
    value = money(raw)
    if value != value.quantize(Decimal("0.001")):
        raise ValueError("Case quantity precision exceeded")
    return value


def value_receipts(assessment: dict, po_lines: list[dict], receipts: list[dict]) -> dict:
    """Allocate landed charges by PO goods value; retain unsold bucket values.

    Damaged and quarantined stock are expressly excluded from available stock;
    their values are *exposure*, not a recognized expense/insurance recovery.
    Partial receipts retain an unreceived value. All figures are estimates.
    """
    snapshot = assessment.get("lines")
    if not isinstance(snapshot, list) or not snapshot or not isinstance(po_lines, list):
        raise ValueError("Missing financial assessment lines")
    ordered: dict[str, Decimal] = {}
    costs: dict[str, Decimal] = {}
    for line in po_lines:
        sku = line.get("sku")
        if not isinstance(sku, str) or not sku or sku in ordered:
            raise ValueError("Invalid PO SKU scope")
        ordered[sku] = _as_case_quantity(line.get("qty_ordered"))
        if ordered[sku] <= 0:
            raise ValueError("Invalid ordered quantity")
    for line in snapshot:
        sku = line.get("sku") if isinstance(line, dict) else None
        if sku not in ordered or sku in costs:
            raise ValueError("Financial assessment does not match PO")
        if _as_case_quantity(line.get("qty_ordered")) != ordered[sku]:
            raise ValueError("Financial assessment quantity drift")
        costs[sku] = money(line.get("unit_cost"))
    if not ordered or set(ordered) != set(costs):
        raise ValueError("Missing or extra financial assessment SKUs")

    landed = money(assessment.get("landed_cost_total"))
    fx = money(assessment.get("fx_rate_to_usd"), allow_zero=False)
    currency = assessment.get("currency")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isupper():
        raise ValueError("Invalid currency")
    if currency == "USD" and fx != 1:
        raise ValueError("USD exchange rate must be 1")
    goods = sum((ordered[s] * costs[s] for s in ordered), Decimal(0))
    if landed and goods == 0:
        raise ValueError("Cannot allocate freight without goods values")

    buckets = {sku: {"available": Decimal(0), "damaged": Decimal(0),
                     "quarantined": Decimal(0)} for sku in ordered}
    for line in receipts:
        sku = line.get("sku")
        if sku not in buckets:
            raise ValueError("Unexpected receipt SKU")
        for source, dest in (("accepted_qty", "available"),
                             ("damaged_qty", "damaged"),
                             ("quarantined_qty", "quarantined")):
            buckets[sku][dest] += _as_case_quantity(line.get(source))
        if sum(buckets[sku].values()) > ordered[sku]:
            raise ValueError("Received more than ordered")

    result_lines = []
    totals = {bucket: Decimal(0) for bucket in ("available", "damaged", "quarantined", "unreceived")}
    for sku in sorted(ordered):
        share = (ordered[sku] * costs[sku] / goods) if goods else Decimal(0)
        allocated = landed * share
        landed_per_case = allocated / ordered[sku]
        per_case = costs[sku] + landed_per_case
        counts = buckets[sku]
        unreceived = ordered[sku] - sum(counts.values())
        values = {bucket: (qty * per_case).quantize(CENT, rounding=ROUND_HALF_UP)
                  for bucket, qty in {**counts, "unreceived": unreceived}.items()}
        for bucket, value in values.items():
            totals[bucket] += value
        result_lines.append({
            "sku": sku, "ordered_cases": str(ordered[sku]),
            "unit_cost": str(costs[sku]),
            "allocated_landed_cost": str(allocated.quantize(CENT, rounding=ROUND_HALF_UP)),
            "cases": {k: str(v) for k, v in {**counts, "unreceived": unreceived}.items()},
            "value_by_bucket": {k: str(v) for k, v in values.items()},
        })

    order_value = (goods + landed).quantize(CENT, rounding=ROUND_HALF_UP)
    usd_values = {k: (v * fx).quantize(CENT, rounding=ROUND_HALF_UP)
                  for k, v in totals.items()}
    usd_order = (order_value * fx).quantize(CENT, rounding=ROUND_HALF_UP)
    damaged_cases = sum((buckets[s]["damaged"] for s in buckets), Decimal(0))
    return {
        "currency": currency, "fx_rate_to_usd": str(fx),
        "goods_cost": str(goods.quantize(CENT, rounding=ROUND_HALF_UP)),
        "landed_cost_total": str(landed.quantize(CENT, rounding=ROUND_HALF_UP)),
        "estimated_order_value": str(order_value),
        "estimated_value_by_bucket": {k: str(v) for k, v in totals.items()},
        "rounding_residual": str(order_value - sum(totals.values())),
        "indicative_usd_order_value": str(usd_order),
        "indicative_usd_value_by_bucket": {k: str(v) for k, v in usd_values.items()},
        "usd_rounding_residual": str(usd_order - sum(usd_values.values())),
        "supplier_claim_candidate": {
            "damaged_cases": str(damaged_cases),
            "estimated_damaged_exposure": str(totals["damaged"]),
            "currency": currency, "claim_status": "unfiled_unverified",
            "supplier_credit_posted": False,
        },
        "lines": result_lines, "valuation_verified": False,
        "accounting_reconciled": False,
        "warning": "Provisional cost claims; damaged/quarantined values are exposures, not sellable inventory or posted supplier credits.",
    }


def compare_qbo_bill(assessment: dict, po_lines: list[dict], bill: dict,
                    expected_vendor_id: str) -> dict:
    """Only classify a strict *candidate* Bill match; never reconcile books."""
    result = {"bill_candidate_matched": False, "accounting_reconciled": False,
              "reason": "source_not_verified"}
    try:
        if not isinstance(bill, dict) or not expected_vendor_id:
            raise ValueError("Missing QBO Bill/vendor")
        vendor = bill.get("VendorRef")
        if not isinstance(vendor, dict) or str(vendor.get("value")) != expected_vendor_id:
            raise ValueError("Vendor mismatch")
        currency = bill.get("CurrencyRef")
        if not isinstance(currency, dict) or currency.get("value") != assessment.get("currency"):
            raise ValueError("Currency mismatch")
        if not isinstance(bill.get("Id"), str) or not bill["Id"].isdigit():
            raise ValueError("Invalid QBO Bill ID")
        mapping = {line["sku"]: str(line.get("qb_item_id") or "") for line in po_lines}
        if not mapping or any(not item_id for item_id in mapping.values()) or len(set(mapping.values())) != len(mapping):
            raise ValueError("Missing unique QBO item mappings")
        snap = assessment["lines"]
        if len(snap) != len(mapping) or len({line["sku"] for line in snap}) != len(mapping):
            raise ValueError("Assessment SKU mismatch")
        expected = {mapping[line["sku"]]: (_as_case_quantity(line["qty_ordered"]), money(line["unit_cost"]))
                    for line in snap}
        actual: dict[str, tuple[Decimal, Decimal]] = {}
        bill_lines = bill.get("Line")
        if not isinstance(bill_lines, list) or not bill_lines:
            raise ValueError("Bill lines absent")
        for line in bill_lines:
            if not isinstance(line, dict) or line.get("DetailType") != "ItemBasedExpenseLineDetail":
                raise ValueError("Unsupported bill line including tax/charge")
            detail = line.get("ItemBasedExpenseLineDetail")
            if not isinstance(detail, dict) or not isinstance(detail.get("ItemRef"), dict):
                raise ValueError("Bill item missing")
            item = str(detail["ItemRef"].get("value") or "")
            if item not in expected or item in actual:
                raise ValueError("Unmapped or duplicate Bill item")
            quantity = _as_case_quantity(detail.get("Qty"))
            price = money(str(detail.get("UnitPrice")))
            amount = money(str(line.get("Amount")))
            if quantity * price != amount or (quantity, price) != expected[item]:
                raise ValueError("Bill quantity, rate or amount mismatch")
            actual[item] = (quantity, price)
        if set(actual) != set(expected):
            raise ValueError("Bill missing items")
        total = sum((qty * price for qty, price in expected.values()), Decimal(0))
        if money(str(bill.get("TotalAmt"))) != total:
            raise ValueError("Bill total mismatch or additional charges")
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        result["reason"] = str(exc)
        return result
    return {"bill_candidate_matched": True, "accounting_reconciled": False,
            "reason": "bill_amounts_match_but_posting_and_source_scope_unverified"}
