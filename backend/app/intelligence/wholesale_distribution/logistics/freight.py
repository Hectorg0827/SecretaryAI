"""
Stage 4 — Freight Booking & Cost Capture.
Stage 5 — Ocean / Ground Transit Tracking.

Freight booking: capture all cost components from forwarder communications.
Transit tracking: vessel position polling with delay detection and inventory
impact analysis.

In production, vessel tracking integrates with:
  • Shipping line APIs (Maersk, MSC, CMA CGM, Hapag-Lloyd)
  • AIS providers (MarineTraffic, VesselFinder)
The adapter pattern below allows easy swapping of the tracking backend.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Protocol

from .pipeline import (
    FreightBooking,
    FreightCostComponent,
    SKUProfile,
    TrackingEvent,
)


# ─── Freight Cost Components ──────────────────────────────────────────────────

# Standard component names (canonical)
FREIGHT_COMPONENTS = {
    "ocean_freight": "Ocean Freight",
    "fuel_surcharge": "Fuel Surcharge / BAF",
    "origin_charges": "Origin Charges (THC, doc)",
    "insurance": "Cargo Insurance",
    "customs_duty": "Customs Duties & Tariffs",
    "broker_fees": "Customs Broker Fees",
    "port_charges": "Port / Destination Terminal",
    "demurrage": "Demurrage & Detention",
    "drayage": "Domestic Drayage",
    "exam_fees": "Customs Exam / Inspection Fees",
}

# Alert thresholds (% change vs. previous shipment) per component
ALERT_THRESHOLDS: dict[str, float] = {
    "ocean_freight": 5.0,
    "fuel_surcharge": 10.0,
    "origin_charges": 10.0,
    "insurance": 15.0,
    "customs_duty": 0.1,     # any duty rate change is notable
    "broker_fees": 10.0,
    "port_charges": 10.0,
    "demurrage": 0.01,        # any demurrage → alert
    "drayage": 10.0,
    "exam_fees": 0.01,        # any exam fee → alert
}


class FreightCostParser:
    """
    Extracts freight cost components from forwarder emails and invoices.
    Uses regex pattern matching on common invoice formats.
    """

    _AMOUNT_PATTERN = re.compile(r"\$?\s*([\d,]+\.?\d{0,2})")
    _COMPONENT_KEYWORDS: dict[str, list[str]] = {
        "ocean_freight": ["ocean freight", "sea freight", "flete marítimo", "flete oceánico"],
        "fuel_surcharge": ["fuel surcharge", "baf", "bunker", "combustible", "recargo combustible"],
        "origin_charges": ["origin charges", "thc", "terminal handling", "documentation", "origen"],
        "insurance": ["insurance", "seguro", "cargo insurance"],
        "customs_duty": ["duty", "tariff", "arancel", "customs duty", "import duty"],
        "broker_fees": ["broker fee", "brokerage", "honorario", "despachante", "customs broker"],
        "port_charges": ["port charge", "destination thc", "cargo de destino", "cargos portuarios"],
        "demurrage": ["demurrage", "detention", "storage", "demora", "almacenaje"],
        "drayage": ["drayage", "cartage", "trucking", "last mile", "transporte terrestre"],
        "exam_fees": ["exam fee", "inspection", "c.e.t.", "border exam", "revisión"],
    }

    def parse_invoice_text(self, text: str, total_cases: int) -> list[FreightCostComponent]:
        """
        Extract cost components from invoice text.
        Allocates per-container costs across cases.
        """
        components: list[FreightCostComponent] = []
        text_lower = text.lower()

        for component_key, keywords in self._COMPONENT_KEYWORDS.items():
            for kw in keywords:
                if kw in text_lower:
                    # Find the amount on the same line
                    for line in text.split("\n"):
                        if kw in line.lower():
                            amounts = self._AMOUNT_PATTERN.findall(line)
                            if amounts:
                                # Take the last (rightmost) amount — usually the total
                                amount_str = amounts[-1].replace(",", "")
                                try:
                                    amount = float(amount_str)
                                    if amount > 0:
                                        components.append(FreightCostComponent(
                                            component=component_key,
                                            amount_usd=amount,
                                            notes=f"Extracted from: {line.strip()[:80]}",
                                        ))
                                        break
                                except ValueError:
                                    pass
                        break  # matched keyword, move to next component
        return components

    def allocate_to_skus(
        self,
        components: list[FreightCostComponent],
        sku_allocations: dict[str, int],  # sku → cases
    ) -> dict[str, dict[str, float]]:
        """
        Allocate total freight costs to individual SKUs proportionally by case count.
        Returns {sku → {component → cost_per_case}}.
        """
        total_cases = sum(sku_allocations.values())
        if total_cases == 0:
            return {}

        result: dict[str, dict[str, float]] = {sku: {} for sku in sku_allocations}

        for comp in components:
            per_case = comp.amount_usd / total_cases
            for sku in sku_allocations:
                result[sku][comp.component] = round(per_case, 4)

        return result


# ─── Vessel Tracking Adapter (Protocol + default implementation) ──────────────


class VesselTrackingAdapter(Protocol):
    """
    Protocol for vessel tracking backends.
    Implement this to integrate with MarineTraffic, Maersk API, etc.
    """

    def get_position(self, container_number: str) -> dict:
        """Returns {lat, lon, status, current_port, updated_eta, vessel_name}."""
        ...

    def get_eta(self, booking_ref: str) -> Optional[str]:
        """Returns ISO date string of updated ETA, or None if unavailable."""
        ...


class StubVesselTracker:
    """
    Stub vessel tracker for development/testing.
    In production, swap this for a real API integration.
    """

    def get_position(self, container_number: str) -> dict:
        return {
            "lat": 35.0,
            "lon": -40.0,
            "status": "in_transit",
            "current_port": None,
            "updated_eta": None,
            "vessel_name": "MV STUB VESSEL",
            "source": "stub",
        }

    def get_eta(self, booking_ref: str) -> Optional[str]:
        return None


# ─── Transit Tracking Engine ─────────────────────────────────────────────────


class TransitTracker:
    """
    Manages real-time tracking of active shipments.
    Detects delays, computes inventory impact, suggests mitigations.
    """

    def __init__(
        self,
        company_id: str,
        vessel_adapter: Optional[VesselTrackingAdapter] = None,
        delay_alert_days: int = 3,
    ):
        self.company_id = company_id
        self._adapter = vessel_adapter or StubVesselTracker()
        self.delay_alert_days = delay_alert_days

    def check_shipment(
        self,
        booking: FreightBooking,
        sku_profiles: list[SKUProfile],  # SKUs in this shipment
        avg_daily_demand_by_sku: dict[str, float],  # sku → cases/day
        avg_margin_per_case_by_sku: dict[str, float] = None,
    ) -> TrackingEvent:
        """
        Query the tracking adapter for current shipment status.
        Returns a TrackingEvent with delay analysis if ETA has changed.
        """
        position = {}
        if booking.container_numbers:
            position = self._adapter.get_position(booking.container_numbers[0])

        updated_eta = (
            self._adapter.get_eta(booking.booking_ref)
            or position.get("updated_eta")
            or booking.eta
        )

        original_eta = booking.eta
        delay_days = self._compute_delay(original_eta, updated_eta)

        # Inventory impact analysis
        impact_text = ""
        mitigations: list[str] = []
        if delay_days >= self.delay_alert_days and sku_profiles:
            impact_text, mitigations = self._analyze_inventory_impact(
                delay_days=delay_days,
                sku_profiles=sku_profiles,
                avg_daily_demand=avg_daily_demand_by_sku,
                avg_margin=avg_margin_per_case_by_sku or {},
                updated_eta=updated_eta,
            )

        event = TrackingEvent(
            booking_id=booking.id,
            vessel_lat=position.get("lat"),
            vessel_lon=position.get("lon"),
            current_port=position.get("current_port", ""),
            status_description=position.get("status", "unknown"),
            updated_eta=updated_eta,
            original_eta=original_eta,
            delay_days=delay_days,
            inventory_impact=impact_text,
            mitigation_options=mitigations,
        )
        return event

    def _analyze_inventory_impact(
        self,
        delay_days: int,
        sku_profiles: list[SKUProfile],
        avg_daily_demand: dict[str, float],
        avg_margin: dict[str, float],
        updated_eta: Optional[str],
    ) -> tuple[str, list[str]]:
        """
        Compute how much the delay affects inventory levels and lost sales.
        """
        impacts: list[str] = []
        total_lost_revenue = 0.0
        at_risk_skus: list[str] = []

        for profile in sku_profiles:
            daily = avg_daily_demand.get(profile.sku, 0.0)
            if daily <= 0:
                continue

            # Days of supply at the ORIGINAL ETA
            days_supply = profile.stock_on_hand / max(daily, 0.001)
            if days_supply < 0:
                days_supply = 0

            # Try to determine when original ETA was
            # Use updated_eta minus delay to approximate original ETA
            try:
                if updated_eta:
                    new_arrival = date.fromisoformat(updated_eta)
                    orig_arrival = new_arrival - timedelta(days=delay_days)
                    gap_days = max(0, delay_days - (days_supply - (new_arrival - date.today()).days))
                else:
                    gap_days = max(0, delay_days - days_supply)
            except (ValueError, TypeError):
                gap_days = max(0, delay_days)

            if gap_days > 0:
                margin = avg_margin.get(profile.sku, 10.0)
                lost_rev = gap_days * daily * margin
                total_lost_revenue += lost_rev
                at_risk_skus.append(
                    f"{profile.name} ({profile.sku}): projected {gap_days:.0f}-day gap, "
                    f"~${lost_rev:,.0f} revenue at risk"
                )

        if not at_risk_skus:
            return (
                f"Container delayed {delay_days} days. Current stock levels sufficient "
                "to cover the delay without stockouts.",
                [],
            )

        impact_text = (
            f"Container delayed {delay_days} days. "
            f"SKUs at stockout risk: {'; '.join(at_risk_skus[:3])}. "
            f"Total estimated revenue impact: ${total_lost_revenue:,.0f}."
        )

        mitigations = [
            "Expedite customs clearance to recover 1–2 days upon arrival",
            "Reduce allocation to non-priority accounts to stretch remaining supply",
        ]
        if any(p.supply_chain_type.value == "domestic" for p in sku_profiles):
            mitigations.append(
                "Source emergency stock from domestic backup supplier at premium cost"
            )
        mitigations.append("Notify key accounts proactively to manage expectations")

        return impact_text, mitigations

    @staticmethod
    def _compute_delay(original_eta: Optional[str], updated_eta: Optional[str]) -> int:
        if not original_eta or not updated_eta:
            return 0
        try:
            orig = date.fromisoformat(original_eta[:10])
            updated = date.fromisoformat(updated_eta[:10])
            return (updated - orig).days
        except (ValueError, TypeError):
            return 0


class FreightCostCapture:
    """
    Captures and validates freight costs from forwarder invoices.
    Stores them as FreightCostComponents on the FreightBooking.
    """

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._parser = FreightCostParser()

    def capture_from_invoice(
        self,
        booking: FreightBooking,
        invoice_text: str,
        total_cases: int,
    ) -> FreightBooking:
        """Parse an invoice and attach cost components to the booking."""
        components = self._parser.parse_invoice_text(invoice_text, total_cases)
        booking.cost_components.extend(components)
        return booking

    def capture_manual(
        self,
        booking: FreightBooking,
        component: str,
        amount_usd: float,
        notes: str = "",
    ) -> FreightBooking:
        """Manually add a cost component to a booking."""
        booking.cost_components.append(
            FreightCostComponent(component=component, amount_usd=amount_usd, notes=notes)
        )
        return booking

    def cost_summary(self, booking: FreightBooking) -> dict:
        """Summarize captured costs."""
        by_component: dict[str, float] = {}
        for c in booking.cost_components:
            by_component[c.component] = by_component.get(c.component, 0) + c.amount_usd
        return {
            "booking_id": booking.id,
            "total_cost": round(booking.total_freight_cost, 2),
            "by_component": {k: round(v, 2) for k, v in by_component.items()},
            "missing_components": [
                c for c in FREIGHT_COMPONENTS
                if c not in by_component
            ],
        }
