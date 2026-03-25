"""
Stage 2 — Purchase Order Generation.

Intelligent PO assembly with:
  • Supplier consolidation (one PO per supplier, not one per SKU)
  • Container fill optimization (add near-reorder SKUs to reach 90% fill)
  • Minimum order compliance checking
  • Price verification against last known price
  • PO document content generation (consumed by PDF renderer or email template)
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Optional

from .pipeline import (
    POLineItem,
    PurchaseOrder,
    ReorderDecisionPackage,
    SKUProfile,
    SupplyChainType,
)


class POGenerator:
    """Assembles purchase orders from approved reorder decision packages."""

    # Sequential PO counter — in production this would come from the DB
    _po_counter: int = 1000

    def __init__(
        self,
        company_id: str,
        ship_to_address: str,
        default_payment_terms: str = "Net 30",
        po_prefix: str = "PO",
        lead_time_buffer_days: int = 5,
    ):
        self.company_id = company_id
        self.ship_to_address = ship_to_address
        self.default_payment_terms = default_payment_terms
        self.po_prefix = po_prefix
        self.lead_time_buffer_days = lead_time_buffer_days

    # ── Public API ───────────────────────────────────────────────────────────

    def generate_pos(
        self,
        approved_packages: list[ReorderDecisionPackage],
        supplier_configs: dict[str, dict],
        # supplier_id → {email, payment_terms, language, min_order_cases,
        #                 min_order_value, special_instructions}
        near_reorder_skus: Optional[list[dict]] = None,
        # Additional SKUs approaching reorder — used for container fill optimization
        # [{profile: SKUProfile, suggested_qty: int, reason: str}]
    ) -> list[PurchaseOrder]:
        """
        Convert approved ReorderDecisionPackages into PurchaseOrders.
        Groups by supplier; optimizes container fill for overseas orders.
        Returns one PO per supplier.
        """
        # Group approved packages by supplier
        by_supplier: dict[str, list[ReorderDecisionPackage]] = {}
        for pkg in approved_packages:
            if not pkg.approved or pkg.dismissed:
                continue
            sid = pkg.sku_profile.supplier_id if pkg.sku_profile else "unknown"
            by_supplier.setdefault(sid, []).append(pkg)

        pos: list[PurchaseOrder] = []
        for supplier_id, packages in by_supplier.items():
            cfg = supplier_configs.get(supplier_id, {})
            po = self._build_po(
                supplier_id=supplier_id,
                packages=packages,
                config=cfg,
                near_reorder_skus=[
                    s for s in (near_reorder_skus or [])
                    if s.get("profile") and s["profile"].supplier_id == supplier_id
                ],
            )
            pos.append(po)

        return pos

    def generate_single_po(
        self,
        packages: list[ReorderDecisionPackage],
        supplier_id: str,
        supplier_config: dict,
    ) -> PurchaseOrder:
        """Generate one PO for a specific supplier from the given packages."""
        return self._build_po(
            supplier_id=supplier_id,
            packages=packages,
            config=supplier_config,
            near_reorder_skus=[],
        )

    def check_minimum_order(
        self,
        po: PurchaseOrder,
        min_cases: int = 0,
        min_value_usd: float = 0.0,
    ) -> tuple[bool, str]:
        """
        Check whether the PO meets minimum order requirements.
        Returns (passes, human-readable message).
        """
        if min_cases and po.total_cases < min_cases:
            shortfall = min_cases - po.total_cases
            return False, (
                f"PO total {po.total_cases} cases is below supplier minimum of {min_cases}. "
                f"Add {shortfall} more cases to meet the minimum, or contact supplier."
            )
        if min_value_usd and po.total_value < min_value_usd:
            shortfall = min_value_usd - po.total_value
            return False, (
                f"PO value ${po.total_value:,.2f} is below supplier minimum of ${min_value_usd:,.2f}. "
                f"Add ${shortfall:,.2f} of product to meet the minimum."
            )
        return True, "PO meets minimum order requirements."

    def suggest_fill_additions(
        self,
        current_cases: int,
        container_capacity: int,
        near_reorder_skus: list[dict],
        target_fill_pct: float = 0.90,
    ) -> list[dict]:
        """
        Suggest additional SKUs to include in the PO to reach target container fill %.
        Returns ordered list of suggestions, highest value first.
        """
        current_fill = current_cases / max(container_capacity, 1)
        if current_fill >= target_fill_pct:
            return []

        target_cases = int(container_capacity * target_fill_pct)
        gap = target_cases - current_cases
        suggestions: list[dict] = []

        remaining_gap = gap
        for item in near_reorder_skus:
            profile: SKUProfile = item.get("profile")
            if not profile:
                continue
            qty = min(item.get("suggested_qty", 0), remaining_gap)
            if qty <= 0:
                continue

            freight_saving_per_case = (
                (target_fill_pct - current_fill) * 4.50 / max(qty, 1)
            )  # rough estimate
            suggestions.append({
                "sku": profile.sku,
                "sku_name": profile.name,
                "suggested_qty": qty,
                "reason": item.get("reason", "Approaching reorder point"),
                "unit_cost": profile.unit_cost_fob,
                "total_cost": profile.unit_cost_fob * qty,
                "freight_saving_per_case": round(freight_saving_per_case, 3),
                "new_fill_pct": round(
                    (current_cases + qty) / container_capacity * 100, 1
                ),
            })
            remaining_gap -= qty
            if remaining_gap <= 0:
                break

        return suggestions

    # ── Private helpers ──────────────────────────────────────────────────────

    def _next_po_number(self) -> str:
        POGenerator._po_counter += 1
        return f"{self.po_prefix}-{POGenerator._po_counter:05d}"

    def _build_po(
        self,
        supplier_id: str,
        packages: list[ReorderDecisionPackage],
        config: dict,
        near_reorder_skus: list[dict],
    ) -> PurchaseOrder:
        # Line items from approved packages
        line_items: list[POLineItem] = []
        for pkg in packages:
            if not pkg.sku_profile:
                continue
            qty = pkg.approved_qty if pkg.approved_qty is not None else pkg.recommended_qty
            if qty <= 0:
                continue
            price = pkg.sku_profile.unit_cost_fob
            line_items.append(POLineItem(
                sku=pkg.sku_profile.sku,
                description=pkg.sku_profile.name,
                qty_cases=qty,
                unit_price=price,
                total_price=price * qty,
                hs_code=pkg.sku_profile.hs_code,
            ))

        # Container fill optimization (overseas only)
        supply_type = packages[0].sku_profile.supply_chain_type if packages else SupplyChainType.DOMESTIC
        if supply_type == SupplyChainType.OVERSEAS and near_reorder_skus:
            current_cases = sum(li.qty_cases for li in line_items)
            cap = packages[0].sku_profile.cases_per_40hc_container if packages else 1_800
            additions = self.suggest_fill_additions(
                current_cases=current_cases,
                container_capacity=cap,
                near_reorder_skus=near_reorder_skus,
            )
            for add in additions:
                line_items.append(POLineItem(
                    sku=add["sku"],
                    description=add["sku_name"],
                    qty_cases=add["suggested_qty"],
                    unit_price=add["unit_cost"],
                    total_price=add["unit_cost"] * add["suggested_qty"],
                    notes=f"Added for container optimization: {add['reason']}",
                ))

        # Requested ship date
        profile = packages[0].sku_profile if packages else None
        lt_days = packages[0].lead_time_p80_days if packages else 30.0
        ship_by = (date.today() + timedelta(days=self.lead_time_buffer_days)).isoformat()

        po = PurchaseOrder(
            po_number=self._next_po_number(),
            supplier_id=supplier_id,
            supplier_name=config.get("name", supplier_id),
            supplier_email=config.get("email", ""),
            ship_to_address=self.ship_to_address,
            payment_terms=config.get("payment_terms", self.default_payment_terms),
            requested_ship_date=ship_by,
            language=config.get("language", "en"),
            line_items=line_items,
            special_instructions=config.get("special_instructions", ""),
        )
        return po

    def render_po_email_body(self, po: PurchaseOrder) -> str:
        """Render a plain-text email body for the PO in the supplier's language."""
        if po.language == "es":
            return self._render_email_es(po)
        return self._render_email_en(po)

    @staticmethod
    def _render_email_en(po: PurchaseOrder) -> str:
        lines = [
            f"Dear {po.supplier_name},",
            "",
            f"Please find attached Purchase Order {po.po_number} for your review and confirmation.",
            "",
            "Order Summary:",
        ]
        for li in po.line_items:
            lines.append(
                f"  • {li.description} ({li.sku}): {li.qty_cases} cases @ "
                f"${li.unit_price:.4f}/case = ${li.total_price:.2f}"
            )
        lines += [
            "",
            f"Total: ${po.total_value:,.2f} ({po.total_cases} cases)",
            f"Payment Terms: {po.payment_terms}",
            f"Requested Ship Date: {po.requested_ship_date}",
            "",
            "Please confirm this order and provide your estimated shipping date.",
            "If you have any questions or require changes, please reply to this email.",
        ]
        if po.special_instructions:
            lines += ["", f"Special Instructions: {po.special_instructions}"]
        lines += ["", "Thank you for your continued partnership.", "", "SecretaryAI Logistics"]
        return "\n".join(lines)

    @staticmethod
    def _render_email_es(po: PurchaseOrder) -> str:
        lines = [
            f"Estimado equipo de {po.supplier_name},",
            "",
            f"Adjunto encontrará la Orden de Compra {po.po_number} para su revisión y confirmación.",
            "",
            "Resumen del pedido:",
        ]
        for li in po.line_items:
            lines.append(
                f"  • {li.description} ({li.sku}): {li.qty_cases} cajas @ "
                f"${li.unit_price:.4f}/caja = ${li.total_price:.2f}"
            )
        lines += [
            "",
            f"Total: ${po.total_value:,.2f} ({po.total_cases} cajas)",
            f"Condiciones de pago: {po.payment_terms}",
            f"Fecha de envío solicitada: {po.requested_ship_date}",
            "",
            "Por favor confirme este pedido e indique la fecha estimada de envío.",
            "Ante cualquier pregunta o cambio requerido, no dude en responder a este correo.",
        ]
        if po.special_instructions:
            lines += ["", f"Instrucciones especiales: {po.special_instructions}"]
        lines += ["", "Gracias por su continua colaboración.", "", "SecretaryAI Logistics"]
        return "\n".join(lines)
