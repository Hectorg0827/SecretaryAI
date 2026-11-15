"""Passive Lola Chief-of-Staff briefing over existing source-backed logistics records.

No QuickBooks or live carrier calls are performed. No write actions are
accepted or emitted. All alerts pass the existing LogisticsViewer projection.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from app.intelligence.wholesale_distribution.logistics.monitoring import LogisticsMonitor
from app.intelligence.wholesale_distribution.logistics.visibility import LogisticsViewer

MAX_CARDS = 20


class BriefingUnavailable(RuntimeError):
    """Company-scoped evidence cannot be trusted."""


def build_chief_of_staff_briefing(*, user: dict, ledger, as_of: date,
                                   limit: int = 5) -> dict:
    if not isinstance(limit, int) or not 1 <= limit <= MAX_CARDS:
        raise ValueError("Card limit outside allowed range")
    if not isinstance(as_of, date) or isinstance(as_of, datetime):
        raise ValueError("A calendar date is required")
    company = str(user.get("company_id") or "")
    if not company or str(getattr(ledger, "company_id", "")) != company:
        raise BriefingUnavailable("Company scope mismatch")
    viewer = LogisticsViewer.from_user(user)
    viewer.require_access()
    shipments = ledger.shipments()
    if any(getattr(s, "company_id", None) != company for s in shipments):
        raise BriefingUnavailable("Shipment tenant mismatch")

    # The monitor derives exceptions; the viewer performs record-level
    # customer and commercial redaction before Lola sees any alert text.
    alerts = LogisticsMonitor(ledger).scan(as_of=as_of)
    scoped = viewer.filter_alerts(alerts, {s.shipment_id: s for s in shipments})
    visible_shipments = viewer.visible(shipments)
    visible_count = len(visible_shipments)
    visible_ids = {s.shipment_id for s in visible_shipments}

    all_events = ledger.events()
    if any(getattr(e, "company_id", None) != company for e in all_events):
        raise BriefingUnavailable("Ledger event tenant mismatch")
    # Event counts/fingerprints are also permission scoped. Sales roles cannot
    # infer activity in shipments they are not assigned to.
    events = [e for e in all_events if getattr(e, "shipment_id", None) in visible_ids]
    fingerprint = hashlib.sha256(
        (company + "\n" + as_of.isoformat() + "\n"
         + "\n".join(sorted(str(e.event_id) for e in events))).encode()
    ).hexdigest()

    cards = []
    for alert in scoped[:limit]:
        estimate = alert.get("financial_exposure_usd")
        cards.append({
            "id": alert["alert_id"],
            "kind": "operational_exception",
            "code": alert["code"],
            "severity": alert["severity"],
            "title": alert["title"],
            "summary": alert["message"],
            "shipment_id": alert.get("shipment_id") or None,
            "priority": alert.get("priority", 0),
            "recommended_actions": alert.get("recommended_actions") or [],
            "has_reviewable_draft": isinstance(alert.get("draft"), dict),
            "action_status": "review_only_not_staged",
            "estimated_financial_exposure_usd": estimate,
            "financial_exposure_kind": (
                "restricted" if estimate is None
                else "model_estimate_not_ledger_reconciled"
            ),
            "evidence_status": (
                "unverified_report_requires_confirmation"
                if alert["code"] == "UNVERIFIED_REPORT"
                else "derived_from_logistics_ledger"
            ),
            "source": {
                "system": "logistics_event_ledger", "alert_id": alert["alert_id"],
                "fingerprint": fingerprint,
            },
            "navigation": "/logistics",
        })

    state = ("no_visible_shipments" if visible_count == 0
             else "needs_attention" if scoped else "no_visible_logistics_exceptions")
    return {
        "state": state,
        "scope": "logistics_only",
        "reconciled_with_quickbooks": False,
        "pricing_approval_verified": False,
        "data_read_at": datetime.now(timezone.utc).isoformat(),
        "evaluated_for_date": as_of.isoformat(),
        "source_event_fingerprint": fingerprint,
        "source_event_count": len(events),
        "visible_shipment_count": visible_count,
        "visible_alert_count": len(scoped),
        "cards_truncated": len(scoped) > limit,
        "cards": cards,
        "proactive_voice_enabled": False,
        "notifications_sent": False,
        "actions_staged": False,
        "external_writes_executed": False,
        "coverage_note": (
            "Logistics events only. QuickBooks, pricing, email and live carrier "
            "feeds were not reconciled. No visible exceptions is not proof "
            "that all business activity is normal."
        ),
    }
