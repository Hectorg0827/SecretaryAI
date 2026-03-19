"""
Purchase Order generator.
Drafts purchase orders for review (DRAFT_AND_WAIT — never sent automatically).
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional


@dataclass
class POLineItem:
    product_name: str
    qb_item_id: Optional[str]
    quantity: int
    unit_cost: Decimal
    total_cost: Decimal = field(init=False)

    def __post_init__(self):
        self.total_cost = Decimal(str(self.quantity)) * self.unit_cost


@dataclass
class PurchaseOrderDraft:
    draft_id: str
    company_id: str
    vendor_name: str
    vendor_id: Optional[str]
    line_items: list[POLineItem]
    subtotal: Decimal
    suggested_delivery_date: Optional[date]
    notes: str
    created_reason: str     # Why this PO is being drafted
    created_at: date

    def to_dict(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "company_id": self.company_id,
            "vendor_name": self.vendor_name,
            "vendor_id": self.vendor_id,
            "line_items": [
                {
                    "product_name": li.product_name,
                    "qb_item_id": li.qb_item_id,
                    "quantity": li.quantity,
                    "unit_cost": float(li.unit_cost),
                    "total_cost": float(li.total_cost),
                }
                for li in self.line_items
            ],
            "subtotal": float(self.subtotal),
            "suggested_delivery_date": self.suggested_delivery_date.isoformat() if self.suggested_delivery_date else None,
            "notes": self.notes,
            "created_reason": self.created_reason,
            "created_at": self.created_at.isoformat(),
            "status": "pending_approval",
        }


def generate_reorder_po(
    company_id: str,
    vendor_name: str,
    vendor_id: Optional[str],
    items_needing_reorder: list[dict],
    lead_time_weeks: int = 6,
    target_weeks_stock: int = 8,
    today: Optional[date] = None,
) -> PurchaseOrderDraft:
    """
    Generate a draft PO for items that need reordering.

    items_needing_reorder: list of dicts with keys:
      product_name, qb_item_id, recommended_order_qty, purchase_cost, weekly_sell_rate
    """
    today = today or date.today()
    line_items: list[POLineItem] = []

    for item in items_needing_reorder:
        qty = max(1, int(item.get("recommended_order_qty", 0)))
        cost = Decimal(str(item.get("purchase_cost", 0)))
        line_items.append(POLineItem(
            product_name=item["product_name"],
            qb_item_id=item.get("qb_item_id"),
            quantity=qty,
            unit_cost=cost,
        ))

    subtotal = sum(li.total_cost for li in line_items)
    delivery_date = today + timedelta(weeks=lead_time_weeks)

    product_names = ", ".join(li.product_name for li in line_items[:3])
    if len(line_items) > 3:
        product_names += f" (+{len(line_items) - 3} more)"

    return PurchaseOrderDraft(
        draft_id=str(uuid.uuid4()),
        company_id=company_id,
        vendor_name=vendor_name,
        vendor_id=vendor_id,
        line_items=line_items,
        subtotal=subtotal,
        suggested_delivery_date=delivery_date,
        notes=f"Auto-drafted based on low stock alerts. Products: {product_names}.",
        created_reason=f"Stock alert: {len(line_items)} product(s) below {target_weeks_stock}-week supply threshold",
        created_at=today,
    )
