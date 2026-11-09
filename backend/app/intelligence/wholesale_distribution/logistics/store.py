"""
Persistence + connector adapters for the logistics journey ledger.

SupabaseEventStore
    Append-only ``logistics_events`` table (migration
    20261109000011_logistics_journey.sql).  Unique (company_id,
    idempotency_key) makes duplicate deliveries harmless at the DB level too.
    Every query carries an explicit ``company_id`` predicate because the
    service-role client bypasses RLS.

parcel_tracking_to_events
    Maps a result from the existing ``ShipTrackConnector`` (FedEx / UPS / DHL)
    onto ledger events for one customer order.  Stub / error / unconfigured
    results produce NO events — a placeholder must never look like tracking data.
"""

from __future__ import annotations

import logging
from typing import Any

from .journey import LogisticsEvent, SourceType

log = logging.getLogger(__name__)

TABLE = "logistics_events"


class SupabaseEventStore:
    def __init__(self, db: Any):
        self._db = db

    def append(self, event: LogisticsEvent) -> None:
        row = event.to_dict()
        try:
            self._db.table(TABLE).upsert(
                row, on_conflict="company_id,idempotency_key", ignore_duplicates=True,
            ).execute()
        except TypeError:
            # Older supabase-py without ignore_duplicates
            self._db.table(TABLE).upsert(row, on_conflict="company_id,idempotency_key").execute()

    def load(self, company_id: str) -> list[LogisticsEvent]:
        res = (
            self._db.table(TABLE).select("*")
            .eq("company_id", company_id)
            .order("recorded_at", desc=False)
            .order("inserted_at", desc=False)
            .execute()
        )
        events = []
        for row in res.data or []:
            if row.get("company_id") != company_id:
                continue  # defence in depth
            try:
                events.append(LogisticsEvent.from_dict(row))
            except (TypeError, ValueError) as exc:
                log.warning("skipping malformed logistics event %s: %s", row.get("event_id"), exc)
        return events


# ─── Parcel carriers (existing ShipTrackConnector) ───────────────────────────

_PARCEL_STATUS = {
    # normalized → (allocation status, milestone code)
    "delivered": ("delivered", "delivered"),
    "dl": ("delivered", "delivered"),
    "d": ("delivered", "delivered"),
    "out_for_delivery": ("out_for_delivery", "out_for_delivery"),
    "od": ("out_for_delivery", "out_for_delivery"),
    "transit": ("out_for_delivery", None),
    "it": ("out_for_delivery", None),
}
_NOT_DATA = {"credentials_not_configured", "error", "unknown", ""}


def parcel_tracking_to_events(company_id: str, shipment_id: str, order_id: str,
                              tracking: dict) -> list[LogisticsEvent]:
    status = str(tracking.get("status", "")).strip().lower()
    if status in _NOT_DATA or not tracking.get("tracking_number"):
        return []
    desc = str(tracking.get("description", "")).lower()
    key = status if status in _PARCEL_STATUS else (
        "delivered" if "delivered" in desc else "out_for_delivery" if "out for delivery" in desc else "")
    if not key:
        return []
    alloc_status, milestone = _PARCEL_STATUS[key]
    when = ""
    events = tracking.get("events") or []
    if events:
        when = str(events[0].get("timestamp", ""))
    ref = f"{tracking.get('carrier', 'carrier')}:{tracking['tracking_number']}"
    out = [LogisticsEvent(
        company_id=company_id, shipment_id=shipment_id, kind="allocation_update",
        source=SourceType.CARRIER_API, occurred_at=when, source_ref=ref,
        payload={"order_id": order_id, "status": alloc_status,
                 "tracking_number": tracking["tracking_number"]},
    )]
    if milestone:
        out.append(LogisticsEvent(
            company_id=company_id, shipment_id=shipment_id, kind="milestone", code=milestone,
            source=SourceType.CARRIER_API, occurred_at=when, source_ref=ref,
            payload={"order_id": order_id},
        ))
    return out
