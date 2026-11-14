"""
Who may see what in logistics data.

Built on Workstream 1's permission interface (``app.auth.rbac.ROLE_PERMISSIONS``)
— no new roles are invented here.  The JWT payload (``user`` dict) is the only
input; client-supplied company ids are never trusted.

Visibility tiers
────────────────
full_operations   ``view_all``                 owner / manager
logistics         ``view_orders`` or            back_office
                  ``view_inventory`` without
                  ``view_own_accounts``
sales             ``view_own_accounts``          sales_rep
none              anything else                 viewer

Commercial data (unit costs, landed cost, freight $, supplier payment terms,
claim values, demurrage $ and supplier performance) requires
``view_financials``.

Customer allocations:
  • full_operations → all customers.
  • logistics       → quantities, order refs, dates and delivery status, but the
                      customer's identity is redacted unless the user's token
                      lists the shipment in ``assigned_shipment_ids``.
  • sales           → only allocations where ``sales_rep_id`` equals the user's
                      ``sub`` or the customer is in the token's
                      ``customer_ids``.  Fails CLOSED: an allocation with no
                      rep on it is not shown to any rep.  Shipments with no
                      such allocation are invisible to the rep.

HR / payroll data is never part of the logistics model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .journey import Shipment
from .monitoring import LogisticsAlert

COMMERCIAL_KEYS = ("commercial", "costs")
SALES_VISIBLE_DOCS = {"proof_of_delivery", "delivery_appointment"}


class LogisticsAccessDenied(PermissionError):
    pass


@dataclass
class LogisticsViewer:
    user_id: str
    company_id: str
    role: str
    tier: str
    can_view_commercial: bool
    customer_ids: set = field(default_factory=set)
    assigned_shipment_ids: set = field(default_factory=set)

    @classmethod
    def from_user(cls, user: dict) -> "LogisticsViewer":
        # Imported lazily: app.auth.rbac pulls in JWT settings, and the
        # intelligence engines must stay importable without app config.
        from app.auth.rbac import ROLE_PERMISSIONS

        role = user.get("role", "viewer")
        perms = ROLE_PERMISSIONS.get(role, set())
        if "view_all" in perms:
            tier = "full_operations"
        elif "view_own_accounts" in perms:
            tier = "sales"
        elif perms & {"view_orders", "view_inventory"}:
            tier = "logistics"
        else:
            tier = "none"
        return cls(
            user_id=str(user.get("sub", "")),
            company_id=str(user.get("company_id", "")),
            role=role, tier=tier,
            can_view_commercial="view_financials" in perms,
            customer_ids=set(user.get("customer_ids") or []),
            assigned_shipment_ids=set(user.get("assigned_shipment_ids") or []),
        )

    # ── checks ───────────────────────────────────────────────────────────────

    def require_access(self) -> None:
        if self.tier == "none":
            raise LogisticsAccessDenied(f"role '{self.role}' has no logistics access")

    @property
    def can_operate(self) -> bool:
        """May record events, confirm evidence, reconcile receipts."""
        return self.tier in ("full_operations", "logistics")

    def owns_allocation(self, alloc) -> bool:
        return bool(
            (alloc.sales_rep_id and alloc.sales_rep_id == self.user_id)
            or (alloc.customer_id and alloc.customer_id in self.customer_ids)
        )

    def can_see_shipment(self, s: Shipment) -> bool:
        if s.company_id != self.company_id or self.tier == "none":
            return False
        if self.tier == "sales":
            return any(self.owns_allocation(a) for a in s.allocations)
        return True

    def visible(self, shipments: list[Shipment]) -> list[Shipment]:
        return [s for s in shipments if self.can_see_shipment(s)]

    # ── projections ──────────────────────────────────────────────────────────

    def project(self, s: Shipment) -> dict:
        """Return the shipment as this viewer is allowed to see it."""
        if not self.can_see_shipment(s):
            raise LogisticsAccessDenied("shipment not visible to this user")
        d = s.to_dict()
        d.pop("company_id", None)

        if not self.can_view_commercial:
            for k in COMMERCIAL_KEYS:
                d.pop(k, None)
            d["lines"] = [{k: v for k, v in l.items() if k != "unit_cost"} for l in d["lines"]]
            d["port_terms"] = {k: v for k, v in d["port_terms"].items() if "rate" not in k}
            d["receipt"] = {k: v for k, v in d["receipt"].items() if not k.endswith("_usd")}
            for x in d["exceptions"]:
                x["data"] = {k: v for k, v in x["data"].items() if "usd" not in k and "value" not in k}

        if self.tier == "logistics":
            assigned = s.shipment_id in self.assigned_shipment_ids
            if not assigned:
                d["allocations"] = [_redact_customer(a) for a in d["allocations"]]

        if self.tier == "sales":
            mine = [a for a in s.allocations if self.owns_allocation(a)]
            my_skus = {a.sku.upper() for a in mine}
            my_docs = {a.pod_doc_id for a in mine if a.pod_doc_id}
            d["allocations"] = [a.to_dict() for a in mine]
            d["lines"] = [l for l in d["lines"] if l["sku"].upper() in my_skus]
            d["total_cases"] = sum(l["qty_cases"] for l in d["lines"])
            d["documents"] = [doc for doc in d["documents"]
                              if doc["doc_type"] in SALES_VISIBLE_DOCS
                              and (doc["doc_type"] != "proof_of_delivery" or doc["doc_id"] in my_docs)]
            for key in ("supplier_id", "supplier_name", "forwarder", "broker", "po_numbers",
                        "receipt", "pending_evidence"):
                d.pop(key, None)
            d["exceptions"] = [x for x in d["exceptions"]
                               if x["code"] in ("CUSTOMS_HOLD",) or x["stage"] in (
                                   "12_final_distribution_delivery", "13_proof_of_delivery")]
            def _clean(m: dict) -> dict:
                # responsible_party embeds counterparty names ("supplier: Vinos …") → role only
                out = {k: v for k, v in m.items() if k not in ("source_ref", "confirmed_by", "notes")}
                out["responsible_party"] = str(m.get("responsible_party", "")).split(":")[0]
                return out

            d["milestones"] = {k: _clean(m) for k, m in d["milestones"].items()}
            for st in d["stages"]:
                st["responsible_party"] = str(st["responsible_party"]).split(":")[0]
                st["unverified_reports"] = []
                st["exceptions"] = [x for x in st["exceptions"] if x["code"] == "CUSTOMS_HOLD"]
                st["verified_milestones"] = [_clean(m) for m in st["verified_milestones"]]
        return d

    def filter_alerts(self, alerts: list[LogisticsAlert], ledger_shipments: dict[str, Shipment]) -> list[dict]:
        out = []
        for a in alerts:
            s = ledger_shipments.get(a.shipment_id) if a.shipment_id else None
            if self.tier == "none":
                continue
            if a.contains_commercial and not self.can_view_commercial:
                if a.code == "SUPPLIER_PERFORMANCE" or a.code == "FREIGHT_COST_VARIANCE":
                    continue
                d = a.to_dict()
                d["financial_exposure_usd"] = None
                d["message"] = _strip_money(d["message"])
            else:
                d = a.to_dict()
            if self.tier == "sales":
                if s is None or not self.can_see_shipment(s):
                    continue
                mine = {x.customer_id for x in s.allocations if self.owns_allocation(x)}
                if a.customer_ids and not (set(a.customer_ids) & mine):
                    continue
                if not a.customer_ids and a.code not in ("SHIPMENT_DELAYED", "CUSTOMS_HOLD"):
                    continue
                d["customer_ids"] = [c for c in a.customer_ids if c in mine]
                d["draft"] = d["draft"] if (d.get("draft") or {}).get("to_role") == "customer" else None
            elif self.tier == "logistics" and s is not None and s.shipment_id not in self.assigned_shipment_ids:
                if d.get("draft") and d["draft"].get("to_role") == "customer":
                    d["draft"] = {**d["draft"], "to_id": "[redacted]"}
                d["customer_ids"] = [f"[{len(a.customer_ids)} customer(s)]"] if a.customer_ids else []
            out.append(d)
        return out


def _redact_customer(a: dict) -> dict:
    return {**a, "customer_id": "[redacted]", "customer_name": "[redacted]", "sales_rep_id": ""}


def _strip_money(msg: str) -> str:
    import re
    return re.sub(r"\$[\d,]+(?:\.\d+)?", "$[restricted]", msg)
