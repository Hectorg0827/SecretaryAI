"""
Translate Lola's WS1 contracts into the Workstream 4 client contract
(``frontend/src/lola/contract.ts`` / ``docs/lola/CONTRACT.md``).

Everything here is presentation of data the orchestrator has ALREADY
authorised and filtered. Nothing in this module reads a data source or decides
access; it only reshapes ``LolaResponse`` / ``ToolResult`` / ``ActionReceipt``.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from app.lola.contracts import (
    ActionProposal,
    ActionReceipt,
    LolaResponse,
    LolaStatus,
    RiskTier,
    Sensitivity,
    ToolResult,
)

_AVAILABILITY = {
    LolaStatus.ANSWERED: "ok", LolaStatus.PARTIAL: "partial", LolaStatus.UNAVAILABLE: "unavailable",
    LolaStatus.DENIED: "forbidden",
}
_SENSITIVITY = {
    Sensitivity.CONFIDENTIAL: "financial", Sensitivity.RESTRICTED: "executive",
}
_RISK = {RiskTier.HIGH_RISK: "high", RiskTier.APPROVAL_REQUIRED: "medium"}
_PREVIEW_STATUS = {
    LolaStatus.DRAFT: "proposed", LolaStatus.PENDING_APPROVAL: "proposed", LolaStatus.APPROVED: "submitted",
    LolaStatus.EXECUTING: "submitted", LolaStatus.COMPLETED: "executed", LolaStatus.FAILED: "failed",
    LolaStatus.REJECTED: "rejected", LolaStatus.NEEDS_RECONCILIATION: "submitted", LolaStatus.DENIED: "failed",
}


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def evidence_for(result: ToolResult, limit: int = 5) -> list[dict]:
    out = []
    for e in result.evidence[:limit]:
        item = {"source": e.source_system.replace("_", " "), "as_of": e.fetched_at.isoformat()}
        if e.record_id:
            item["record_ref"] = e.record_id
        out.append(item)
    return out


def availability_for(result: ToolResult) -> str:
    if result.status == LolaStatus.ANSWERED and any(e.is_stale for e in result.evidence):
        return "stale"
    return _AVAILABILITY.get(result.status, "unavailable")


def card_for(result: ToolResult) -> Optional[dict]:
    """A WS4 EvidenceCard for an answered/partial tool result (None for withheld ones)."""
    if result.status not in (LolaStatus.ANSWERED, LolaStatus.PARTIAL):
        return None
    d = result.data if isinstance(result.data, dict) else {}
    base = {
        "id": result.tool, "title": result.summary[:120] or result.tool,
        "availability": availability_for(result), "evidence": evidence_for(result),
        "sensitivity": _SENSITIVITY.get(result.sensitivity, "standard"),
    }
    if result.warnings:
        base["availability_note"] = " ".join(result.warnings)[:300]
    hidden = set(result.authorization.redacted_fields)

    if result.tool == "sales.customer_accounts":
        cols = [{"key": "name", "label": "Account"}, {"key": "state", "label": "State"}]
        if "balance" not in hidden:
            cols.append({"key": "balance", "label": "Balance", "align": "right"})
        rows = [{k: (_money(a.get(k)) if k == "balance" else (a.get(k) or "—")) for k in [c["key"] for c in cols]}
                for a in d.get("accounts", [])[:25]]
        return {**base, "kind": "table", "title": "Accounts", "columns": cols, "rows": rows}
    if result.tool == "sales.performance":
        return {**base, "kind": "metrics", "title": f"Sales — last {d.get('days')} days",
                "metrics": [{"label": "Orders", "value": str(d.get("order_count", 0))},
                            {"label": "Revenue", "value": _money(d.get("total_revenue"))}]}
    if result.tool == "finance.receivables":
        return {**base, "kind": "metrics", "title": "Receivables", "sensitivity": "financial",
                "metrics": [{"label": "Open invoices", "value": str(d.get("open_invoices", 0))},
                            {"label": "Open balance", "value": _money(d.get("open_balance"))},
                            {"label": "Overdue", "value": _money(d.get("overdue_balance")), "tone": "bad"
                             if d.get("overdue_invoices") else "neutral"}]}
    if result.tool == "operations.inventory":
        cols = [{"key": "product_name", "label": "Product"}, {"key": "total_qty", "label": "On hand", "align": "right"},
                {"key": "reorder_point", "label": "Reorder at", "align": "right"}]
        rows = [{c["key"]: (i.get(c["key"]) if i.get(c["key"]) is not None else "—") for c in cols}
                for i in d.get("items", [])[:25]]
        return {**base, "kind": "table", "title": "Inventory", "columns": cols, "rows": rows}
    if result.tool == "purchasing.open_orders":
        cols = [{"key": "qb_id", "label": "PO"}, {"key": "vendor_name", "label": "Vendor"},
                {"key": "expected_date", "label": "Expected"}]
        rows = [{c["key"]: (p.get(c["key"]) or "—") for c in cols} for p in d.get("purchase_orders", [])[:25]]
        return {**base, "kind": "table", "title": "Open purchase orders", "columns": cols, "rows": rows}
    if result.tool == "hr.directory":
        cols = [{"key": "name", "label": "Name"}, {"key": "role", "label": "Role"}]
        rows = [{"name": e.get("name") or "—", "role": e.get("role") or "—"} for e in d.get("employees", [])[:50]]
        return {**base, "kind": "table", "title": "Team", "sensitivity": "executive", "columns": cols, "rows": rows}
    if result.tool.startswith("bi."):
        findings = [f for f in (d.get("findings") or []) if isinstance(f, dict)]
        rows = [{"statement": str(f.get("statement", "")), "basis": str(f.get("basis", ""))} for f in findings[:15]]
        return {**base, "kind": "table", "title": result.tool[3:].replace("_", " ").capitalize(),
                "columns": [{"key": "statement", "label": "Finding"}, {"key": "basis", "label": "Basis"}],
                "rows": rows}
    return None  # logistics.assistant and others answer in text


def notice_for(result: ToolResult) -> Optional[dict]:
    if result.status == LolaStatus.DENIED:
        return {"kind": "permission_denied", "message": result.summary}
    if result.status == LolaStatus.UNAVAILABLE:
        kind = "connector_outage" if result.error_code in ("source_not_connected", "source_error") else "data_unavailable"
        return {"kind": kind, "message": result.summary, "connector": result.department.value}
    return None


def action_preview(receipt: ActionReceipt, proposal: Optional[ActionProposal], *,
                   can_approve: bool, approval_note: Optional[str], show_amount: bool = True) -> dict:
    payload = proposal.payload if proposal else {}
    fields = []
    if proposal and proposal.entity:
        fields.append({"label": "Account", "value": proposal.entity.display_name})
    # Exactly what would be executed, so the approver reviews the real thing.
    if payload.get("to"):
        fields.append({"label": "To", "value": str(payload["to"])})
    if payload.get("subject"):
        fields.append({"label": "Subject", "value": str(payload["subject"])[:200]})
    if "vendor_id" in payload:
        fields.append({"label": "Vendor", "value": str(payload.get("vendor_name") or payload.get("vendor_id")
                                                         or "Missing — add before approving")})
    for line in (payload.get("lines") or [])[:5]:
        fields.append({"label": "Item", "value": f"{line.get('quantity')} × {line.get('description') or line.get('item_id')}"})
    if proposal and proposal.amount is not None and show_amount:
        fields.append({"label": "Amount", "value": _money(proposal.amount)})
    if payload.get("instruction"):
        fields.append({"label": "Request", "value": str(payload["instruction"])[:200]})
    fields.append({"label": "Approval", "value": "Independent leadership approval required"
                   if receipt.risk_tier == RiskTier.HIGH_RISK else "Manager or owner approval"})
    out = {
        "id": receipt.receipt_id, "action_type": receipt.action_type,
        "title": proposal.title if proposal else receipt.action_type.replace("_", " ").capitalize(),
        "summary": "Prepared by Lola. Nothing is sent until it is approved and verified.",
        "fields": fields, "risk": _RISK.get(receipt.risk_tier, "low"),
        "can_approve": bool(can_approve and receipt.status == LolaStatus.PENDING_APPROVAL),
        "status": _PREVIEW_STATUS.get(receipt.status, "proposed"),
    }
    if approval_note and not out["can_approve"]:
        out["approval_note"] = approval_note
    return out


def client_receipt(receipt: ActionReceipt, message: Optional[str] = None) -> dict:
    """WS4 Receipt. ``executed`` only when the receipt is completed AND verified."""
    if receipt.status == LolaStatus.COMPLETED and receipt.verified:
        status, default = "executed", "Done and verified."
    elif receipt.status in (LolaStatus.FAILED, LolaStatus.REJECTED, LolaStatus.DENIED):
        status, default = "failed", "This did not happen. Nothing was sent."
    else:
        status, default = "submitted", "Accepted, awaiting confirmation. Nothing is confirmed as sent yet."
    out = {"action_id": receipt.receipt_id, "status": status,
           "verified": bool(status == "executed"), "message": message or default}
    if receipt.external_reference and status == "executed":
        out["reference"] = receipt.external_reference
        out["executed_at"] = receipt.updated_at.isoformat()
    return out


def turn_events(resp: LolaResponse, previews: Iterable[dict], *, suggestions: Iterable[str] = ()) -> list[dict]:
    events: list[dict] = [{"type": "conversation", "conversation_id": resp.conversation_id}]
    for r in resp.tool_results:
        events.append({"type": "status", "state": "working", "step": f"Checked {r.department.value}: {r.tool}"})
    events.append({"type": "text", "delta": resp.answer})
    for r in resp.tool_results:
        card = card_for(r)
        if card:
            events.append({"type": "card", "card": card})
    seen_notices: set[str] = set()
    for r in resp.tool_results:
        n = notice_for(r)
        if n and n["message"] not in seen_notices:
            seen_notices.add(n["message"])
            events.append({"type": "notice", "notice": n})
    for p in previews:
        events.append({"type": "action_preview", "action": p})
    for rc in resp.receipts:
        if rc.status == LolaStatus.COMPLETED and rc.verified:
            events.append({"type": "receipt", "receipt": client_receipt(rc)})
    sugg = [s for s in suggestions][:4]
    if sugg:
        events.append({"type": "suggestions", "items": sugg})
    events.append({"type": "done"})
    return events


def suggestions_for(resp: LolaResponse) -> list[str]:
    tools = {t.tool for t in resp.tool_results if t.status in (LolaStatus.ANSWERED, LolaStatus.PARTIAL)}
    out = []
    if "sales.customer_accounts" in tools and resp.entities:
        out.append(f"Draft a follow-up email to {resp.entities[0].display_name}")
    if "operations.inventory" in tools:
        out.append("Which items are below their reorder point?")
    if "finance.receivables" in tools:
        out.append("Who should we prioritise for collections?")
    if not out:
        out = ["How are my accounts doing?", "What's running low in inventory?", "Where are my shipments?"]
    return out[:4]
