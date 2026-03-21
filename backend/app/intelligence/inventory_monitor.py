"""
Inventory level monitoring and stock status classification.
Tracks weeks-of-supply and flags items needing reorder.

Thresholds and terminology are loaded from the active IndustryModule —
no values are hardcoded here. Changing the module changes the thresholds.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class InventoryStatus:
    item_id: str
    product_name: str
    total_qty: int
    warehouse_1_qty: int
    warehouse_2_qty: int
    weekly_sell_rate: Decimal
    weeks_remaining: Optional[Decimal]
    status: str        # healthy | low | critical | out_of_stock
    needs_po: bool
    message: str


# Module-loaded defaults — used when no industry module is available
# (e.g. in tests without a full DB context)
_DEFAULT_CRITICAL_WEEKS = Decimal("2")
_DEFAULT_LOW_WEEKS = Decimal("4")
_DEFAULT_UNIT = "units"


def _load_thresholds(industry_module=None) -> tuple[Decimal, Decimal, str]:
    """
    Load inventory thresholds and unit from the industry module.
    Falls back to defaults if no module is provided.
    """
    if industry_module is None:
        return _DEFAULT_CRITICAL_WEEKS, _DEFAULT_LOW_WEEKS, _DEFAULT_UNIT

    kpis = industry_module.get_kpi_definitions()
    inv_kpi = kpis.get("inventory_critical_weeks")
    terminology = industry_module.get_terminology()
    unit = terminology.get("unit_of_measure", _DEFAULT_UNIT)

    if inv_kpi:
        critical = Decimal(str(inv_kpi.critical_threshold))
        low = Decimal(str(inv_kpi.warning_threshold))
    else:
        critical = _DEFAULT_CRITICAL_WEEKS
        low = _DEFAULT_LOW_WEEKS

    return critical, low, unit


def evaluate_inventory(
    item_id: str,
    product_name: str,
    warehouse_1_qty: int,
    warehouse_2_qty: int,
    weekly_sell_rate: Decimal,
    open_po_qty: int = 0,
    industry_module=None,
) -> InventoryStatus:
    """
    Evaluate inventory status for a single item.

    industry_module: optional IndustryModule instance to load thresholds from.
    When None, falls back to module defaults (backward compatible).
    """
    critical_weeks, low_weeks, unit = _load_thresholds(industry_module)
    total = warehouse_1_qty + warehouse_2_qty

    if weekly_sell_rate > 0:
        weeks_remaining = Decimal(total) / weekly_sell_rate
    else:
        weeks_remaining = None  # not selling

    if total == 0:
        status = "out_of_stock"
        needs_po = True
        message = f"{product_name}: OUT OF STOCK. Immediate PO required."
    elif weeks_remaining is not None and weeks_remaining <= critical_weeks:
        status = "critical"
        needs_po = open_po_qty == 0
        wks = float(weeks_remaining)
        message = (
            f"{product_name}: CRITICAL — {total} {unit}, ~{wks:.1f} weeks left."
            + (" No PO in system." if needs_po else f" {open_po_qty} {unit} on order.")
        )
    elif weeks_remaining is not None and weeks_remaining <= low_weeks:
        status = "low"
        needs_po = open_po_qty == 0
        wks = float(weeks_remaining)
        message = (
            f"{product_name}: LOW — {total} {unit}, ~{wks:.1f} weeks left."
            + (" Consider placing PO." if needs_po else "")
        )
    else:
        status = "healthy"
        needs_po = False
        wks = float(weeks_remaining) if weeks_remaining else 0
        message = f"{product_name}: {total} {unit}, ~{wks:.0f} weeks supply."

    return InventoryStatus(
        item_id=item_id,
        product_name=product_name,
        total_qty=total,
        warehouse_1_qty=warehouse_1_qty,
        warehouse_2_qty=warehouse_2_qty,
        weekly_sell_rate=weekly_sell_rate,
        weeks_remaining=weeks_remaining,
        status=status,
        needs_po=needs_po,
        message=message,
    )


# ─── Backward-compatible constants (for code that reads these directly) ───────
# These reflect the wholesale_distribution module defaults.
# New code should call evaluate_inventory(industry_module=module) instead.
CRITICAL_WEEKS = _DEFAULT_CRITICAL_WEEKS
LOW_WEEKS = _DEFAULT_LOW_WEEKS
