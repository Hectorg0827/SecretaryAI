"""
Connect Workstream 3's receiving reconciliation to the protected stock path.

A warehouse count reconciled by WS3 produces *proposed* stock postings
(good cases → available, damaged cases → damaged). Until now those proposals
sat in the approval queue with no way to be carried out. When
``SECRETARYAI_LOLA_STOCK_STAGING_ENABLED=1`` this module hands them to the
existing, hardened database path instead:

    stage_warehouse_receipt          (the person who counted = submitter)
        → approve_staged_warehouse_receipt   (a DIFFERENT active owner/manager)
            → receive_warehouse_purchase_order → post_stock_movement

All the important rules are enforced inside PostgreSQL, not here: the PO must
be approved and open, every SKU must be on the PO, no line may be over-received,
the approver must be independent of the submitter, stock movements are
immutable and posted exactly once. This module only decides whether a count is
clean enough to stage, and builds a stable receipt key so re-submitting the
same count never stages it twice.

What it never does: post stock without the second approval, touch QuickBooks,
pay anyone, or send anything. Stock is booked in Lion BI's own stock ledger;
``accounting_reconciled`` stays false.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

OPEN_PO_STATUSES = ("approved", "partially_received")


def staging_enabled() -> bool:
    return os.environ.get("SECRETARYAI_LOLA_STOCK_STAGING_ENABLED", "").strip() == "1"


@dataclass
class StagePlan:
    ok: bool
    reason: str                                   # stable code, see REASONS
    po_number: Optional[str] = None
    lines: list[dict] = field(default_factory=list)
    receipt_key: Optional[str] = None


REASONS = {
    "staged": "Staged for receiving approval. A different owner or manager must approve it before stock is posted.",
    "off": "Stock staging is switched off; the postings stay in the approval queue for review only.",
    "no_open_po": "No approved, open purchase order matches this shipment, so nothing was staged.",
    "ambiguous_po": "This shipment matches more than one open purchase order, so nothing was staged.",
    "sku_not_on_po": "Some counted items aren't on the purchase order, so nothing was staged.",
    "over_receipt": "The count is more than what is still open on the purchase order, so nothing was staged.",
    "nothing_counted": "No cases were counted, so nothing was staged.",
    "unavailable": "Purchasing records are unavailable right now, so nothing was staged.",
    "not_permitted": "Your role can't stage warehouse receipts.",
    "refused": "The database refused this receipt (it may already be staged differently), so nothing was staged.",
}


def _rows(query) -> list[dict]:
    data = query.execute().data
    if not isinstance(data, list):
        raise ValueError("unexpected response")
    return data


def receipt_key(company_id: str, shipment_id: str, po_number: str, lines: list[dict]) -> str:
    """Same count for the same shipment and PO → same key (the database replays it)."""
    canonical = json.dumps({"c": company_id, "s": shipment_id, "po": po_number, "l": lines},
                           sort_keys=True, separators=(",", ":"))
    return "lola-rcv:" + hashlib.sha256(canonical.encode()).hexdigest()[:40]


def _aggregate(received: Iterable[Any]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for r in received:
        sku = str(getattr(r, "sku", "") or "").strip()
        lot = str(getattr(r, "lot", "") or "").strip()[:120]
        good = int(getattr(r, "good_cases", 0) or 0)
        damaged = int(getattr(r, "damaged_cases", 0) or 0)
        if not sku or good < 0 or damaged < 0:
            continue
        agg = out.setdefault((sku.upper(), lot), {"good": 0, "damaged": 0})
        agg["good"] += good
        agg["damaged"] += damaged
    return out


def plan(db, *, company_id: str, shipment_id: str, po_numbers: list[str], received: Iterable[Any]) -> StagePlan:
    """Decide whether this count can be staged, and exactly what would be staged."""
    if not staging_enabled():
        return StagePlan(False, "off")
    counted = {k: v for k, v in _aggregate(received).items() if v["good"] or v["damaged"]}
    if not counted:
        return StagePlan(False, "nothing_counted")
    wanted = sorted({str(p).strip() for p in po_numbers if str(p).strip()})
    if not wanted:
        return StagePlan(False, "no_open_po")
    try:
        orders = [o for o in _rows(db.table("purchasing_orders").select("id,po_number,status")
                                   .eq("company_id", company_id).in_("po_number", wanted).limit(5))
                  if o.get("status") in OPEN_PO_STATUSES]
        if not orders:
            return StagePlan(False, "no_open_po")
        if len(orders) > 1:
            return StagePlan(False, "ambiguous_po")
        po = orders[0]
        po_lines = _rows(db.table("purchasing_order_lines").select("sku,qty_ordered,qty_received")
                         .eq("company_id", company_id).eq("po_id", po["id"]).limit(201))
    except Exception:  # noqa: BLE001 — any read failure means "don't stage"
        return StagePlan(False, "unavailable")

    by_upper = {str(l["sku"]).upper(): l for l in po_lines}
    remaining = {k: float(l.get("qty_ordered") or 0) - float(l.get("qty_received") or 0)
                 for k, l in by_upper.items()}
    lines: list[dict] = []
    for (sku_u, lot), q in sorted(counted.items()):
        if sku_u not in by_upper:
            return StagePlan(False, "sku_not_on_po")
        remaining[sku_u] -= q["good"] + q["damaged"]
        if remaining[sku_u] < 0:
            return StagePlan(False, "over_receipt")
        lines.append({"sku": by_upper[sku_u]["sku"], "lot_code": lot,
                      "accepted_qty": q["good"], "damaged_qty": q["damaged"], "quarantined_qty": 0})
    key = receipt_key(company_id, shipment_id, po["po_number"], lines)
    return StagePlan(True, "staged", po_number=po["po_number"], lines=lines, receipt_key=key)


def stage(db, p: StagePlan, *, company_id: str, actor_id: str) -> dict:
    """Stage the receipt (pending). Raises nothing: returns a status dict."""
    assert p.ok and p.po_number and p.receipt_key
    try:
        data = db.rpc("stage_warehouse_receipt", {
            "p_company_id": company_id, "p_po_number": p.po_number, "p_receipt_key": p.receipt_key,
            "p_lines": p.lines, "p_actor": actor_id,
        }).execute().data
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", ""))
        reason = "not_permitted" if code == "42501" else ("refused" if code in ("23505", "23514", "22023", "P0002")
                                                          else "unavailable")
        return {"staged": False, "reason": reason, "message": REASONS[reason]}
    row = data[0] if isinstance(data, list) and data else data
    staged_id = row.get("staged_receipt_id") if isinstance(row, dict) else None
    if not staged_id:
        return {"staged": False, "reason": "unavailable", "message": REASONS["unavailable"]}
    return {
        "staged": True, "reason": "staged", "message": REASONS["staged"],
        "staged_receipt_id": str(staged_id), "po_number": p.po_number,
        "status": row.get("status", "pending"), "replayed": bool(row.get("replayed")),
        "inventory_posted": bool(row.get("inventory_posted")),
        "approve_path": f"/api/logistics/receipts/{staged_id}/approve",
        "lines": p.lines,
    }


STOCK_ACTIONS = frozenset({"logistics.post_inventory_receipt", "logistics.post_inventory_quarantine"})
