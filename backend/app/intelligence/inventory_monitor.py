"""
Inventory level monitoring and stock status classification.
Tracks weeks-of-supply and flags items needing reorder.
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


CRITICAL_WEEKS = Decimal("2")
LOW_WEEKS = Decimal("4")


def evaluate_inventory(
    item_id: str,
    product_name: str,
    warehouse_1_qty: int,
    warehouse_2_qty: int,
    weekly_sell_rate: Decimal,
    open_po_qty: int = 0,
) -> InventoryStatus:
    total = warehouse_1_qty + warehouse_2_qty

    if weekly_sell_rate > 0:
        weeks_remaining = Decimal(total) / weekly_sell_rate
    else:
        weeks_remaining = None  # not selling

    if total == 0:
        status = "out_of_stock"
        needs_po = True
        message = f"{product_name}: OUT OF STOCK. Immediate PO required."
    elif weeks_remaining is not None and weeks_remaining <= CRITICAL_WEEKS:
        status = "critical"
        needs_po = open_po_qty == 0
        wks = float(weeks_remaining)
        message = (
            f"{product_name}: CRITICAL — {total} cases, ~{wks:.1f} weeks left."
            + (" No PO in system." if needs_po else f" {open_po_qty} cases on order.")
        )
    elif weeks_remaining is not None and weeks_remaining <= LOW_WEEKS:
        status = "low"
        needs_po = open_po_qty == 0
        wks = float(weeks_remaining)
        message = (
            f"{product_name}: LOW — {total} cases, ~{wks:.1f} weeks left."
            + (" Consider placing PO." if needs_po else "")
        )
    else:
        status = "healthy"
        needs_po = False
        wks = float(weeks_remaining) if weeks_remaining else 0
        message = f"{product_name}: {total} cases, ~{wks:.0f} weeks supply."

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
