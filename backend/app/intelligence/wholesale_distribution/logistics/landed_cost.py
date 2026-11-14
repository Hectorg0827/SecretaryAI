"""
Landed cost per case from the shipment ledger, with an honesty label.

Result is "final" only when every expected cost component is present and every
cost line came from a verified source (ERP / forwarder / broker feed, or a
reviewed document).  Otherwise it is "estimated" and says what is missing.
Damaged cases do not disappear from cost: the per-saleable-case figure spreads
the full cost over the good cases actually received, which is the number
finance needs when pricing what can be sold.
"""

from __future__ import annotations

from typing import Optional

from .journey import Shipment

# Components an ocean import normally carries before it can be called final.
EXPECTED_IMPORT_COMPONENTS = ("ocean_freight", "customs_duty", "broker_fees", "drayage")
EXPECTED_DOMESTIC_COMPONENTS = ("freight",)


def landed_cost(shipment: Shipment, quoted_freight_usd: Optional[float] = None) -> dict:
    cases = shipment.total_cases
    product_cost = sum(l.qty_cases * l.unit_cost for l in shipment.lines)
    by_component: dict[str, float] = {}
    unverified: list[str] = []
    for c in shipment.costs:
        by_component[c.component] = round(by_component.get(c.component, 0.0) + c.amount_usd, 2)
        if not c.verified and c.component not in unverified:
            unverified.append(c.component)

    expected = EXPECTED_IMPORT_COMPONENTS if shipment.is_import else EXPECTED_DOMESTIC_COMPONENTS
    if not shipment.is_import and "freight" not in by_component:
        # Domestic freight may be recorded under any freight-like name.
        if any(k in by_component for k in ("ocean_freight", "drayage", "ground_freight")):
            expected = ()
    missing = [c for c in expected if c not in by_component]
    logistics_cost = round(sum(by_component.values()), 2)
    total = round(product_cost + logistics_cost, 2)

    good = shipment.receipt.get("good_cases") if shipment.receipt else None
    status = "final" if (not missing and not unverified and product_cost > 0) else "estimated"
    out = {
        "shipment_id": shipment.shipment_id,
        "po_numbers": list(shipment.po_numbers),
        "cases_ordered": cases,
        "product_cost_usd": round(product_cost, 2),
        "logistics_cost_usd": logistics_cost,
        "cost_by_component_usd": by_component,
        "total_landed_cost_usd": total,
        "landed_cost_per_case_usd": round(total / cases, 4) if cases else None,
        "logistics_cost_per_case_usd": round(logistics_cost / cases, 4) if cases else None,
        "saleable_cases_received": good,
        "landed_cost_per_saleable_case_usd": round(total / good, 4) if good else None,
        "status": status,
        "missing_components": missing,
        "unverified_components": unverified,
    }
    quote = quoted_freight_usd if quoted_freight_usd is not None else shipment.commercial.get("freight_quote_usd")
    if quote:
        freight_actual = sum(v for k, v in by_component.items()
                             if k in ("ocean_freight", "fuel_surcharge", "origin_charges", "port_charges",
                                      "freight", "drayage"))
        out["freight_quote_usd"] = float(quote)
        out["freight_invoiced_usd"] = round(freight_actual, 2)
        # No freight invoiced yet → no variance (not "-100%").
        out["freight_variance_pct"] = (
            round((freight_actual - float(quote)) / float(quote) * 100, 1) if freight_actual > 0 else None)
    return out
