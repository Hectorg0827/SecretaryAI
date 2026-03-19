"""
Sync Health Monitor — reconciles data across systems.
Detects discrepancies between QuickBooks and other data sources
(warehouse systems, ordering platforms, ScribeBase, etc.).
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class SyncDiscrepancy:
    check_type: str
    field: str
    source_1_label: str
    source_1_value: float
    source_2_label: str
    source_2_value: float
    variance_pct: float
    severity: str  # "info" | "warning" | "critical"
    message: str


WARNING_THRESHOLD_PCT = 5.0    # 5% variance = warning
CRITICAL_THRESHOLD_PCT = 15.0  # 15% variance = critical


def reconcile_inventory(
    qb_items: list[dict],
    warehouse_items: list[dict],
) -> list[SyncDiscrepancy]:
    """
    Compare QB inventory quantities vs warehouse system quantities.
    Returns a list of discrepancies found.
    """
    discrepancies: list[SyncDiscrepancy] = []

    qb_by_name = {item["product_name"].lower(): item for item in qb_items}
    wh_by_name = {item["product_name"].lower(): item for item in warehouse_items}

    # Check QB items against warehouse
    for name_key, qb_item in qb_by_name.items():
        wh_item = wh_by_name.get(name_key)
        if wh_item is None:
            continue  # Item not in warehouse system — skip

        qb_qty = float(qb_item.get("qb_qty", qb_item.get("quantity_on_hand", 0)))
        wh_qty = float(wh_item.get("quantity", wh_item.get("warehouse_qty", 0)))

        if qb_qty == 0 and wh_qty == 0:
            continue

        reference = max(qb_qty, wh_qty)
        variance_pct = abs(qb_qty - wh_qty) / reference * 100 if reference else 0

        if variance_pct >= WARNING_THRESHOLD_PCT:
            severity = "critical" if variance_pct >= CRITICAL_THRESHOLD_PCT else "warning"
            discrepancies.append(SyncDiscrepancy(
                check_type="inventory_reconciliation",
                field=qb_item["product_name"],
                source_1_label="QuickBooks",
                source_1_value=qb_qty,
                source_2_label="Warehouse System",
                source_2_value=wh_qty,
                variance_pct=round(variance_pct, 1),
                severity=severity,
                message=(
                    f"{qb_item['product_name']}: QB shows {qb_qty:.0f} units, "
                    f"warehouse shows {wh_qty:.0f} units "
                    f"({variance_pct:.1f}% variance)"
                ),
            ))

    return discrepancies


def reconcile_revenue(
    qb_revenue: Decimal,
    scribebase_revenue: Optional[Decimal],
    period_label: str = "current period",
) -> Optional[SyncDiscrepancy]:
    """
    Compare QB revenue vs ordering system (ScribeBase) revenue for the same period.
    """
    if scribebase_revenue is None:
        return None

    qb_val = float(qb_revenue)
    sb_val = float(scribebase_revenue)

    if qb_val == 0 and sb_val == 0:
        return None

    reference = max(qb_val, sb_val)
    variance_pct = abs(qb_val - sb_val) / reference * 100 if reference else 0

    if variance_pct < WARNING_THRESHOLD_PCT:
        return None

    severity = "critical" if variance_pct >= CRITICAL_THRESHOLD_PCT else "warning"
    return SyncDiscrepancy(
        check_type="revenue_reconciliation",
        field=f"Revenue ({period_label})",
        source_1_label="QuickBooks",
        source_1_value=qb_val,
        source_2_label="ScribeBase",
        source_2_value=sb_val,
        variance_pct=round(variance_pct, 1),
        severity=severity,
        message=(
            f"Revenue mismatch for {period_label}: "
            f"QB: ${qb_val:,.2f} vs ScribeBase: ${sb_val:,.2f} "
            f"({variance_pct:.1f}% variance)"
        ),
    )


def summarize_sync_health(discrepancies: list[SyncDiscrepancy]) -> dict:
    """Build a summary dict for the dashboard."""
    if not discrepancies:
        return {"status": "healthy", "discrepancy_count": 0, "discrepancies": []}

    has_critical = any(d.severity == "critical" for d in discrepancies)
    return {
        "status": "critical" if has_critical else "warning",
        "discrepancy_count": len(discrepancies),
        "discrepancies": [
            {
                "type": d.check_type,
                "field": d.field,
                "severity": d.severity,
                "message": d.message,
                "variance_pct": d.variance_pct,
            }
            for d in discrepancies
        ],
    }
