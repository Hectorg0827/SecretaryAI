"""
Build purchase-order drafts that are specific enough to be created in
QuickBooks once a person approves them.

A draft names: the QuickBooks vendor, and for each line the QuickBooks item,
the quantity and the unit cost. Everything is looked up in QuickBooks — a
vendor or item ID the caller sends is only accepted if QuickBooks has it.
When something is missing (no vendor chosen, no cost on file) the draft is
still created, without lines, and approval tells the user to enter the order
by hand rather than guessing.
"""
from __future__ import annotations

import logging
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

log = logging.getLogger(__name__)

TARGET_WEEKS_OF_STOCK = 8
MAX_QUANTITY = 1_000_000


class POBuildError(ValueError):
    """The request names a vendor or item QuickBooks does not have."""


def _dec(value: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def suggested_quantity(on_hand: Any, weekly_sell_rate: Any) -> int:
    """Enough to reach TARGET_WEEKS_OF_STOCK weeks of supply (at least 1)."""
    rate = _dec(weekly_sell_rate) or Decimal(0)
    have = _dec(on_hand) or Decimal(0)
    if rate <= 0:
        return 1
    need = rate * TARGET_WEEKS_OF_STOCK - have
    return max(1, min(MAX_QUANTITY, math.ceil(need)))


async def _find_item(adapter, item_id: str) -> Optional[dict]:
    """The merged inventory row for a QuickBooks item, or None."""
    try:
        items = await adapter.get_inventory_merged()
    except Exception as exc:
        log.warning("PO builder: inventory lookup failed: %s", type(exc).__name__)
        return None
    for it in items or []:
        if str(it.get("qb_id") or "") == str(item_id):
            return it
    return None


async def _preferred_vendor_id(adapter, item_id: str) -> Optional[str]:
    try:
        for it in await adapter.get_inventory_qb():
            if str(getattr(it, "qb_id", "")) == str(item_id):
                return getattr(it, "preferred_vendor_id", None)
    except Exception:
        pass
    return None


async def _vendors(adapter) -> list[dict]:
    try:
        vendors = await adapter.get_vendors()
    except Exception as exc:
        log.warning("PO builder: vendor lookup failed: %s", type(exc).__name__)
        return []
    out = [
        {"id": str(getattr(v, "qb_id", "")), "name": getattr(v, "name", "") or "", "email": getattr(v, "email", None)}
        for v in vendors or []
    ]
    return sorted((v for v in out if v["id"]), key=lambda v: v["name"].lower())


async def po_options(adapter, item_id: str) -> dict:
    """What the 'Draft PO' form needs: the item, its usual vendor, all vendors."""
    item = await _find_item(adapter, item_id)
    vendors = await _vendors(adapter)
    preferred = await _preferred_vendor_id(adapter, item_id) if item else None
    if preferred and not any(v["id"] == str(preferred) for v in vendors):
        preferred = None
    return {
        "item": None if not item else {
            "id": str(item.get("qb_id")),
            "name": item.get("product_name") or "",
            "unit_cost": float(item.get("purchase_cost") or 0),
            "on_hand": float(item.get("total_qty") or 0),
            "weekly_sell_rate": float(item.get("weekly_sell_rate") or 0),
            "suggested_qty": suggested_quantity(item.get("total_qty"), item.get("weekly_sell_rate")),
        },
        "vendors": [{"id": v["id"], "name": v["name"]} for v in vendors],
        "preferred_vendor_id": str(preferred) if preferred else None,
    }


async def build_po_content(
    adapter,
    *,
    item_id: str,
    product_name: str = "",
    vendor_id: Optional[str] = None,
    qty: Optional[Any] = None,
    unit_cost: Optional[Any] = None,
    notes: Optional[str] = None,
    use_preferred_vendor: bool = False,
) -> dict:
    """
    Content for a purchase-order draft.

    Returns a dict that always has item_id / product_name / qty / notes, and —
    when the vendor, quantity and cost are all known — also vendor_id,
    vendor_name, line_items and total, which is what lets approval create the
    order in QuickBooks.

    Raises POBuildError if a vendor_id is given that QuickBooks does not have.
    """
    item = await _find_item(adapter, item_id)
    name = (item or {}).get("product_name") or product_name or ""

    quantity = _dec(qty)
    if quantity is None or quantity <= 0:
        quantity = Decimal(suggested_quantity((item or {}).get("total_qty"), (item or {}).get("weekly_sell_rate")))
    quantity = min(quantity, Decimal(MAX_QUANTITY))

    cost = _dec(unit_cost)
    if cost is None or cost < 0:
        cost = _dec((item or {}).get("purchase_cost"))

    content: dict = {
        "item_id": str(item_id),
        "product_name": name,
        "qty": int(quantity) if quantity == quantity.to_integral_value() else float(quantity),
        "notes": (notes or f"Purchase order for {name}").strip()[:500],
    }

    vendors = await _vendors(adapter)
    chosen = None
    if vendor_id:
        chosen = next((v for v in vendors if v["id"] == str(vendor_id)), None)
        if chosen is None:
            raise POBuildError("That vendor was not found in QuickBooks.")
    elif use_preferred_vendor and item:
        preferred = await _preferred_vendor_id(adapter, item_id)
        chosen = next((v for v in vendors if preferred and v["id"] == str(preferred)), None)

    # Lines are only attached when everything QuickBooks needs is known:
    # a real item, a real vendor, and a cost greater than zero.
    if item and chosen and cost is not None and cost > 0:
        content.update({
            "vendor_id": chosen["id"],
            "vendor_name": chosen["name"],
            "line_items": [{
                "item_id": str(item_id),
                "product_name": name,
                "description": name,
                "quantity": content["qty"],
                "unit_cost": float(cost),
            }],
            "total": float((quantity * cost).quantize(Decimal("0.01"))),
        })
    else:
        missing = []
        if not item:
            missing.append("the item was not found in QuickBooks")
        if not chosen:
            missing.append("no vendor is chosen")
        if cost is None or cost <= 0:
            missing.append("no unit cost is on file")
        content["incomplete_reason"] = "; ".join(missing)
    return content
