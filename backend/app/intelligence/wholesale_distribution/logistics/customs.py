"""
Stage 6 — Port Arrival & Customs Clearance.
Stage 7 — Domestic Drayage & Warehouse Receipt.

Customs: milestone tracking, demurrage risk calculation, documentation issue
         detection. Builds a per-port / per-broker clearance performance DB.

Receipt: confirmation from QuickBooks sync, email, or manual input.
         Captures full stage-by-stage lead time breakdown.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .pipeline import (
    CustomsEvent,
    FreightBooking,
    ReceiptLineItem,
    WarehouseReceipt,
)


# ─── Customs Clearance Engine ─────────────────────────────────────────────────

# Average clearance times by product category (days) — used for performance comparison
CLEARANCE_BENCHMARKS: dict[str, dict[str, float]] = {
    "wine_spirits": {"standard": 3.0, "ttb_review": 7.0},
    "food": {"standard": 2.5, "fda_review": 5.0},
    "general_merchandise": {"standard": 1.5},
    "default": {"standard": 3.0},
}

# Documentation issues that trigger immediate Red alert
CRITICAL_DOC_ISSUES = [
    "missing certificate of origin",
    "incorrect hs code",
    "label violation",
    "fda hold",
    "ttb hold",
    "missing coa",        # Certificate of Analysis
    "missing coo",        # Certificate of Origin
    "documentation error",
    "additional documents",
]


class CustomsMonitor:
    """
    Tracks customs clearance milestones and flags risks in real time.
    """

    def __init__(
        self,
        company_id: str,
        free_time_days: int = 5,           # Free time before demurrage starts
        demurrage_rate_per_day: float = 150.0,
    ):
        self.company_id = company_id
        self.free_time_days = free_time_days
        self.demurrage_rate = demurrage_rate_per_day

    # ── Milestone Logging ────────────────────────────────────────────────────

    def log_arrival(self, booking_id: str, notes: str = "") -> CustomsEvent:
        return CustomsEvent(
            booking_id=booking_id,
            milestone="arrived_at_port",
            notes=notes,
        )

    def log_entry_filed(self, booking_id: str, notes: str = "") -> CustomsEvent:
        return CustomsEvent(
            booking_id=booking_id,
            milestone="entry_filed",
            notes=notes,
        )

    def log_duties_paid(self, booking_id: str, amount: float, notes: str = "") -> CustomsEvent:
        return CustomsEvent(
            booking_id=booking_id,
            milestone="duties_paid",
            notes=f"Duties paid: ${amount:,.2f}. {notes}".strip(),
        )

    def log_hold(
        self,
        booking_id: str,
        hold_reason: str,
        days_at_port: int,
    ) -> tuple[CustomsEvent, dict]:
        """Log a customs hold and return an alert dict."""
        demurrage_days = max(0, days_at_port - self.free_time_days)
        event = CustomsEvent(
            booking_id=booking_id,
            milestone="hold_placed",
            hold_reason=hold_reason,
            demurrage_accruing=days_at_port >= self.free_time_days,
            demurrage_rate_per_day=self.demurrage_rate,
            demurrage_days=demurrage_days,
        )

        is_critical = any(issue in hold_reason.lower() for issue in CRITICAL_DOC_ISSUES)
        alert = {
            "severity": "critical" if is_critical else "red",
            "booking_id": booking_id,
            "hold_reason": hold_reason,
            "demurrage_current": event.demurrage_total,
            "demurrage_rate_per_day": self.demurrage_rate,
            "days_at_port": days_at_port,
            "message": (
                f"Container on customs hold: {hold_reason}. "
                f"Demurrage accruing at ${self.demurrage_rate}/day. "
                f"Current exposure: ${event.demurrage_total:,.0f}."
                + (" CRITICAL — documentation issue may add weeks." if is_critical else "")
            ),
        }
        return event, alert

    def log_released(self, booking_id: str, days_at_port: int, notes: str = "") -> CustomsEvent:
        return CustomsEvent(
            booking_id=booking_id,
            milestone="released",
            notes=notes,
            demurrage_accruing=False,
            demurrage_rate_per_day=self.demurrage_rate,
            demurrage_days=max(0, days_at_port - self.free_time_days),
        )

    # ── Demurrage Risk ───────────────────────────────────────────────────────

    def compute_demurrage_risk(
        self,
        booking_id: str,
        arrival_date_str: str,
        today_str: Optional[str] = None,
    ) -> dict:
        """
        Calculate current demurrage exposure given arrival date and today's date.
        Returns a risk assessment dict.
        """
        try:
            arrival = date.fromisoformat(arrival_date_str[:10])
            today = date.fromisoformat(today_str[:10]) if today_str else date.today()
            days_at_port = (today - arrival).days
        except (ValueError, TypeError):
            days_at_port = 0

        free_remaining = max(0, self.free_time_days - days_at_port)
        demurrage_days = max(0, days_at_port - self.free_time_days)
        current_cost = demurrage_days * self.demurrage_rate

        severity = "info"
        if days_at_port >= self.free_time_days - 1:
            severity = "warning"
        if demurrage_days > 0:
            severity = "alert"
        if demurrage_days > 7:
            severity = "critical"

        return {
            "booking_id": booking_id,
            "days_at_port": days_at_port,
            "free_time_remaining_days": free_remaining,
            "demurrage_days": demurrage_days,
            "current_demurrage_cost": round(current_cost, 2),
            "daily_accrual": self.demurrage_rate if demurrage_days > 0 else 0,
            "severity": severity,
            "action": (
                "Expedite clearance immediately — every day costs more."
                if demurrage_days > 0
                else f"Free time expires in {free_remaining} day(s). Expedite clearance."
                if free_remaining <= 2
                else "Within free time — no action needed yet."
            ),
        }

    # ── Email Parsing for Broker Communications ──────────────────────────────

    def parse_broker_email(self, booking_id: str, subject: str, body: str) -> Optional[CustomsEvent]:
        """
        Parse a customs broker email to extract clearance milestone.
        Returns a CustomsEvent if a milestone is detected.
        """
        text = f"{subject}\n{body}".lower()

        if any(kw in text for kw in ["entry filed", "entrada presentada", "filing submitted"]):
            return self.log_entry_filed(booking_id, notes=f"Detected from broker email: {subject[:60]}")

        if any(kw in text for kw in ["duties paid", "aranceles pagados", "duty payment"]):
            return self.log_duties_paid(booking_id, 0.0, notes=f"From broker email: {subject[:60]}")

        if any(kw in text for kw in ["released", "cleared", "liberado", "despachado", "released from customs"]):
            return self.log_released(booking_id, days_at_port=0, notes=f"From broker email: {subject[:60]}")

        if any(kw in text for kw in ["hold", "retención", "exam", "revisión", "inspection", "fda", "ttb", "detained"]):
            # Extract hold reason
            for issue in CRITICAL_DOC_ISSUES:
                if issue in text:
                    _, alert = self.log_hold(booking_id, issue.title(), days_at_port=0)
                    return self.log_hold(booking_id, issue.title(), days_at_port=0)[0]
            return self.log_hold(
                booking_id,
                "Customs hold — reason under investigation",
                days_at_port=0,
            )[0]

        return None  # No recognizable milestone

    # ── Historical Performance ────────────────────────────────────────────────

    def assess_clearance_performance(
        self,
        events: list[CustomsEvent],
        product_category: str = "default",
    ) -> dict:
        """
        Evaluate how quickly this container moved through customs vs. benchmarks.
        """
        filed_at = next(
            (e.timestamp for e in events if e.milestone == "entry_filed"), None
        )
        released_at = next(
            (e.timestamp for e in events if e.milestone == "released"), None
        )

        if not filed_at or not released_at:
            return {"status": "incomplete", "clearance_days": None}

        try:
            filed = datetime.fromisoformat(filed_at.replace("Z", "+00:00"))
            released = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
            clearance_days = (released - filed).total_seconds() / 86400
        except (ValueError, TypeError):
            clearance_days = None

        benchmarks = CLEARANCE_BENCHMARKS.get(product_category, CLEARANCE_BENCHMARKS["default"])
        benchmark_days = benchmarks.get("standard", 3.0)

        had_hold = any(e.milestone == "hold_placed" for e in events)
        total_demurrage = sum(e.demurrage_total for e in events)

        rating = "excellent"
        if clearance_days:
            if had_hold:
                rating = "delayed"
            elif clearance_days > benchmark_days * 2:
                rating = "slow"
            elif clearance_days > benchmark_days * 1.3:
                rating = "below_benchmark"
            elif clearance_days <= benchmark_days * 0.8:
                rating = "excellent"
            else:
                rating = "on_benchmark"

        return {
            "clearance_days": round(clearance_days, 1) if clearance_days else None,
            "benchmark_days": benchmark_days,
            "rating": rating,
            "had_hold": had_hold,
            "total_demurrage": round(total_demurrage, 2),
        }


# ─── Warehouse Receipt Processor ─────────────────────────────────────────────


class ReceiptProcessor:
    """
    Handles warehouse receipt confirmation for Stage 7.
    Captures full lead time breakdown and triggers Stage 8 reconciliation.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id

    def process_receipt(
        self,
        booking: FreightBooking,
        po_number: str,
        line_items_received: list[dict],
        # [{sku, qty_ordered, qty_received, condition_notes}]
        received_by: str = "system",
        notes: str = "",
        po_sent_at: Optional[str] = None,
        supplier_ship_date: Optional[str] = None,
    ) -> WarehouseReceipt:
        """
        Create a WarehouseReceipt and compute stage-by-stage lead time.
        """
        items = [
            ReceiptLineItem(
                sku=i["sku"],
                qty_ordered=i.get("qty_ordered", 0),
                qty_received=i.get("qty_received", 0),
                qty_short=max(0, i.get("qty_ordered", 0) - i.get("qty_received", 0)),
                condition_notes=i.get("condition_notes", ""),
            )
            for i in line_items_received
        ]

        total_received = sum(li.qty_received for li in items)
        receipt = WarehouseReceipt(
            po_number=po_number,
            booking_id=booking.id,
            received_by=received_by,
            line_items=items,
            total_cases_received=total_received,
            notes=notes,
        )

        # ── Compute lead time breakdown ──────────────────────────────────────
        today_str = date.today().isoformat()

        receipt.po_to_ship_days = self._days_between(po_sent_at, booking.atd or booking.etd)
        receipt.ship_to_arrival_days = self._days_between(
            booking.atd or booking.etd, booking.ata or booking.eta
        )
        receipt.arrival_to_customs_days = self._days_between(
            booking.ata or booking.eta, supplier_ship_date  # proxy for customs release
        )
        receipt.customs_to_warehouse_days = self._days_between(
            supplier_ship_date, today_str
        )

        total = sum(
            d for d in [
                receipt.po_to_ship_days,
                receipt.ship_to_arrival_days,
                receipt.arrival_to_customs_days,
                receipt.customs_to_warehouse_days,
            ]
            if d is not None
        )
        receipt.total_lead_time_days = total if total > 0 else self._days_between(po_sent_at, today_str)

        return receipt

    def detect_shortages(self, receipt: WarehouseReceipt) -> list[dict]:
        """Return list of shorted line items."""
        return [
            {
                "sku": li.sku,
                "qty_ordered": li.qty_ordered,
                "qty_received": li.qty_received,
                "shortage": li.qty_short,
                "pct_filled": round(li.qty_received / max(li.qty_ordered, 1) * 100, 1),
            }
            for li in receipt.line_items
            if li.qty_short > 0
        ]

    @staticmethod
    def _days_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
        if not start or not end:
            return None
        try:
            s = date.fromisoformat(start[:10])
            e = date.fromisoformat(end[:10])
            return max(0.0, float((e - s).days))
        except (ValueError, TypeError):
            return None
