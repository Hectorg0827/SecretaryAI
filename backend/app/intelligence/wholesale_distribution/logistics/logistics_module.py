"""
LogisticsModule — Top-level orchestrator for the 9-Stage Procurement Pipeline.

Mirrors the WholesaleDistributionModule pattern: one class, one entry point.
The agent layer calls LogisticsModule methods; it never imports sub-engines directly.

Instantiate once per company session:
    module = LogisticsModule(company_id="acme-123", config=LogisticsConfig(...))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from .pipeline import (
    FreightBooking,
    LandedCostBreakdown,
    ProcurementCycle,
    ProcurementStage,
    PurchaseOrder,
    SKUProfile,
    SupplyChainType,
    UrgencyLevel,
)
from .reorder_engine import ReorderEngine
from .po_generator import POGenerator
from .vendor_tracker import VendorResponseParser, VendorTracker
from .freight import FreightCostCapture, TransitTracker
from .customs import CustomsMonitor, ReceiptProcessor
from .cost_reconciliation import CostReconciliationEngine
from .learning import LandedCostBreakdown as LCDType, LearningEngine, LeadTimeRecord
from .notifications import AlertType, LogisticsAlert, NotificationRouter


# ─── Configuration ────────────────────────────────────────────────────────────


@dataclass
class LogisticsConfig:
    """
    Company-level knobs for the logistics pipeline.
    Sensible defaults apply for every field.
    """
    # Reorder engine
    service_level_target: float = 0.95          # 95th-percentile safety stock

    # PO generation
    container_target_fill_pct: float = 0.90     # 90% fill before suggesting add-ons
    po_language: str = "en"                     # "en" | "es" for email body

    # Vendor tracking
    overseas_response_sla_hours: int = 72
    domestic_response_sla_hours: int = 24

    # Customs / demurrage
    port_free_time_days: int = 5
    demurrage_rate_per_day: float = 150.0

    # Cost reconciliation
    target_gross_margin_pct: float = 30.0
    shipments_per_year: int = 12

    # Notifications — maps role names → list of email addresses
    # e.g. {"owner": ["ceo@co.com"], "logistics_manager": ["ops@co.com"]}
    role_email_map: dict[str, list[str]] = field(default_factory=dict)

    # Delay alert threshold (days) for transit tracking
    delay_alert_days: int = 3


# ─── LogisticsModule ──────────────────────────────────────────────────────────


class LogisticsModule:
    """
    Unified interface to the 9-stage logistics & procurement intelligence pipeline.

    All sub-engines are wired up at construction time.  Caller never imports
    reorder_engine, po_generator, etc. directly.

    Usage:
        lm = LogisticsModule("acme-123", config)

        # Stage 1 — demand monitoring
        queue = lm.evaluate_reorder_queue(sku_data)

        # Stage 2 — generate POs
        pos = lm.generate_purchase_orders(approved_packages, supplier_configs)

        # … and so on through the pipeline
    """

    def __init__(
        self,
        company_id: str,
        config: Optional[LogisticsConfig] = None,
    ):
        self.company_id = company_id
        self.config = config or LogisticsConfig()

        cfg = self.config

        # Stage 1
        self._reorder = ReorderEngine(company_id=company_id)

        # Stage 2
        self._po_gen = POGenerator(
            company_id=company_id,
            ship_to_address="",   # set per-PO via supplier_configs["ship_to_address"]
        )

        # Stage 3
        self._vendor_parser = VendorResponseParser()
        self._vendor_tracker = VendorTracker(
            company_id=company_id,
            follow_up_hours_overseas=cfg.overseas_response_sla_hours,
            follow_up_hours_domestic=cfg.domestic_response_sla_hours,
        )

        # Stages 4–5
        self._freight_capture = FreightCostCapture(company_id=company_id)
        self._transit_tracker = TransitTracker(
            company_id=company_id,
            delay_alert_days=cfg.delay_alert_days,
        )

        # Stages 6–7
        self._customs = CustomsMonitor(
            company_id=company_id,
            free_time_days=cfg.port_free_time_days,
            demurrage_rate_per_day=cfg.demurrage_rate_per_day,
        )
        self._receipt = ReceiptProcessor(company_id=company_id)

        # Stage 8
        self._reconciliation = CostReconciliationEngine(
            company_id=company_id,
            target_gross_margin_pct=cfg.target_gross_margin_pct,
            shipments_per_year=cfg.shipments_per_year,
        )

        # Stage 9
        self._learning = LearningEngine(company_id=company_id)
        self._lead_time_records: list[LeadTimeRecord] = []

        # Notifications
        self._notifier = NotificationRouter(
            company_id=company_id,
            role_email_map=cfg.role_email_map,
        )

    # ── Stage 1 — Demand Monitoring & Reorder Intelligence ───────────────────

    def evaluate_reorder_queue(
        self,
        sku_data: list[dict],
    ) -> list[dict]:
        """
        Evaluate the full SKU portfolio for reorder needs.

        Each element of ``sku_data`` must contain:
            profile              : SKUProfile
            daily_sales_history  : list[float]   (trailing 90 days min)
            lead_time_history    : list[float]   (historical lead times in days)
        Optional keys:
            seasonal_indices          : list[float]   52-week multipliers
            last_fob_price            : float
            last_freight_cost_per_case: float

        Returns a list of dicts with:
            package  : ReorderDecisionPackage
            alert    : LogisticsAlert (if urgency RED or YELLOW)
        """
        packages = self._reorder.build_reorder_queue(sku_data)

        results = []
        for pkg in packages:
            item: dict = {"package": pkg, "alert": None}

            sku_name = pkg.sku_profile.sku if pkg.sku_profile else "Unknown"
            if pkg.urgency in (UrgencyLevel.RED, UrgencyLevel.YELLOW):
                alert = self._notifier.reorder_alert(
                    sku_name=sku_name,
                    sku=sku_name,
                    days_of_supply=pkg.days_of_supply,
                    lead_time_days=pkg.lead_time_p80_days or 0.0,
                    urgency=pkg.urgency.value,
                )
                item["alert"] = alert

            results.append(item)

        return results

    # ── Stage 2 — Purchase Order Generation ──────────────────────────────────

    def generate_purchase_orders(
        self,
        approved_packages: list,       # list[ReorderDecisionPackage]
        supplier_configs: dict,        # supplier_id → {min_order_cases, currency, ...}
        near_reorder_skus: Optional[list] = None,  # list[SKUProfile] for fill suggestions
    ) -> list[dict]:
        """
        Generate POs from approved reorder packages.

        Returns list of dicts:
            po          : PurchaseOrder
            email_body  : str
        """
        pos = self._po_gen.generate_pos(
            approved_packages=approved_packages,
            supplier_configs=supplier_configs,
            near_reorder_skus=near_reorder_skus,
        )

        results = []
        for po in pos:
            email_body = self._po_gen.render_po_email_body(po)
            results.append({"po": po, "email_body": email_body})

        return results

    # ── Stage 3 — PO Transmission & Vendor Response Tracking ─────────────────

    def parse_vendor_response(
        self,
        po: PurchaseOrder,
        email_subject: str,
        email_body: str,
    ) -> dict:
        """
        Parse an inbound vendor email response for a given PO.

        Returns:
            parsed  : ParsedVendorResponse
            alert   : LogisticsAlert
        """
        parsed = self._vendor_parser.parse(email_subject, email_body, po)

        if parsed.rejection:
            alert = self._notifier.build_alert(
                AlertType.PO_REJECTED,
                context={
                    "po_number": po.po_number,
                    "supplier_name": getattr(po, "supplier_name", po.supplier_id),
                },
            )
        elif parsed.requires_human_review:
            alert = self._notifier.build_alert(
                AlertType.PO_FOLLOW_UP_NEEDED,
                context={
                    "po_number": po.po_number,
                    "supplier_name": getattr(po, "supplier_name", po.supplier_id),
                    "hours_since_sent": 0,
                },
            )
        else:
            alert = self._notifier.po_confirmed_alert(
                po_number=po.po_number,
                supplier_name=getattr(po, "supplier_name", po.supplier_id),
                ship_date=parsed.ship_date,
                total_value=getattr(po, "total_value", 0.0),
            )

        return {"parsed": parsed, "alert": alert}

    def check_overdue_vendors(
        self,
        pos_with_metadata: list[dict],
        # [{po: PurchaseOrder, sent_at: ISO str, supply_chain_type: str, follow_up_count: int}]
    ) -> list[dict]:
        """
        Check which POs are overdue for vendor response and what to do.

        Returns list of escalation action dicts from VendorTracker.
        """
        return self._vendor_tracker.check_overdue_pos(pos_with_metadata)

    # ── Stage 4 — Freight Booking & Cost Capture ──────────────────────────────

    def capture_freight_invoice(
        self,
        booking: FreightBooking,
        invoice_text: str,
        total_cases: int,
    ) -> FreightBooking:
        """
        Parse a forwarder invoice and attach cost components to the booking.
        Returns the updated FreightBooking.
        """
        return self._freight_capture.capture_from_invoice(booking, invoice_text, total_cases)

    def add_freight_cost_manual(
        self,
        booking: FreightBooking,
        component: str,
        amount_usd: float,
        notes: str = "",
    ) -> FreightBooking:
        """Manually add a freight cost component to a booking."""
        return self._freight_capture.capture_manual(booking, component, amount_usd, notes)

    def freight_cost_summary(self, booking: FreightBooking) -> dict:
        """Summarize captured freight costs and list missing components."""
        return self._freight_capture.cost_summary(booking)

    # ── Stage 5 — Transit Tracking ────────────────────────────────────────────

    def check_shipment_status(
        self,
        booking: FreightBooking,
        sku_profiles: list[SKUProfile],
        avg_daily_demand_by_sku: dict[str, float],
        avg_margin_per_case_by_sku: Optional[dict[str, float]] = None,
    ) -> dict:
        """
        Query vessel tracking for current status.

        Returns:
            tracking_event  : TrackingEvent
            alert           : LogisticsAlert | None  (if delay >= threshold)
        """
        event = self._transit_tracker.check_shipment(
            booking=booking,
            sku_profiles=sku_profiles,
            avg_daily_demand_by_sku=avg_daily_demand_by_sku,
            avg_margin_per_case_by_sku=avg_margin_per_case_by_sku or {},
        )

        alert = None
        if event.delay_days >= self.config.delay_alert_days:
            import re
            financial_impact = 0.0
            if event.inventory_impact:
                match = re.search(r"\$([0-9,]+)", event.inventory_impact)
                if match:
                    try:
                        financial_impact = float(match.group(1).replace(",", ""))
                    except ValueError:
                        pass

            alert = self._notifier.shipment_delay_alert(
                po_number=booking.po_number or booking.booking_ref,
                supplier_name=booking.vessel_name or "Unknown vessel",
                original_eta=event.original_eta or "",
                updated_eta=event.updated_eta or "",
                delay_days=event.delay_days,
                at_risk_skus=[p.sku for p in sku_profiles if p.stock_on_hand < 100],
                estimated_revenue_impact=financial_impact,
            )

        return {"tracking_event": event, "alert": alert}

    # ── Stage 6 — Port Arrival & Customs Clearance ────────────────────────────

    def log_port_arrival(self, booking_id: str, notes: str = "") -> dict:
        """Log container arrival at port. Returns CustomsEvent."""
        return {"event": self._customs.log_arrival(booking_id, notes)}

    def log_entry_filed(self, booking_id: str, notes: str = "") -> dict:
        """Log customs entry filing. Returns CustomsEvent."""
        return {"event": self._customs.log_entry_filed(booking_id, notes)}

    def log_duties_paid(self, booking_id: str, amount: float, notes: str = "") -> dict:
        """Log duty payment. Returns CustomsEvent."""
        return {"event": self._customs.log_duties_paid(booking_id, amount, notes)}

    def log_customs_hold(
        self,
        booking_id: str,
        hold_reason: str,
        days_at_port: int,
    ) -> dict:
        """
        Log a customs hold.

        Returns:
            event   : CustomsEvent
            alert   : LogisticsAlert  (always — holds are high priority)
        """
        event, hold_alert_dict = self._customs.log_hold(booking_id, hold_reason, days_at_port)

        is_doc_issue = hold_alert_dict.get("severity") == "critical"
        alert_type = AlertType.CUSTOMS_DOC_ISSUE if is_doc_issue else AlertType.CUSTOMS_HOLD

        alert = self._notifier.build_alert(
            alert_type=alert_type,
            context={
                "booking_id": booking_id,
                "hold_reason": hold_reason,
                "days_at_port": days_at_port,
                "demurrage_current": hold_alert_dict.get("demurrage_current", 0),
                "demurrage_rate_per_day": self.config.demurrage_rate_per_day,
            },
            financial_impact_usd=hold_alert_dict.get("demurrage_current", 0),
        )

        return {"event": event, "alert": alert}

    def check_demurrage_risk(
        self,
        booking_id: str,
        arrival_date_str: str,
        today_str: Optional[str] = None,
    ) -> dict:
        """
        Compute current demurrage exposure.

        Returns risk dict with severity and action recommendation.
        Also returns an alert if severity is 'alert' or 'critical'.
        """
        risk = self._customs.compute_demurrage_risk(booking_id, arrival_date_str, today_str)

        alert = None
        if risk["severity"] in ("alert", "critical"):
            alert = self._notifier.build_alert(
                alert_type=AlertType.DEMURRAGE_RISK,
                context={
                    "booking_id": booking_id,
                    "days_at_port": risk["days_at_port"],
                    "current_demurrage_cost": risk["current_demurrage_cost"],
                    "daily_accrual": risk["daily_accrual"],
                    "free_time_remaining_days": risk["free_time_remaining_days"],
                },
                financial_impact_usd=risk["current_demurrage_cost"],
            )

        return {"risk": risk, "alert": alert}

    def parse_broker_email(
        self,
        booking_id: str,
        subject: str,
        body: str,
    ) -> dict:
        """
        Parse a customs broker email for clearance milestones.

        Returns:
            event    : CustomsEvent | None
            milestone: str | None
        """
        event = self._customs.parse_broker_email(booking_id, subject, body)
        return {
            "event": event,
            "milestone": event.milestone if event else None,
        }

    def log_customs_released(
        self,
        booking_id: str,
        days_at_port: int,
        notes: str = "",
    ) -> dict:
        """Log container release from customs."""
        event = self._customs.log_released(booking_id, days_at_port, notes)
        return {"event": event}

    # ── Stage 7 — Warehouse Receipt ───────────────────────────────────────────

    def process_warehouse_receipt(
        self,
        booking: FreightBooking,
        po_number: str,
        line_items_received: list[dict],
        received_by: str = "system",
        notes: str = "",
        po_sent_at: Optional[str] = None,
        supplier_ship_date: Optional[str] = None,
    ) -> dict:
        """
        Record warehouse receipt and compute stage-by-stage lead time.

        Returns:
            receipt     : WarehouseReceipt
            shortages   : list[dict]
            alert       : LogisticsAlert
        """
        receipt = self._receipt.process_receipt(
            booking=booking,
            po_number=po_number,
            line_items_received=line_items_received,
            received_by=received_by,
            notes=notes,
            po_sent_at=po_sent_at,
            supplier_ship_date=supplier_ship_date,
        )

        shortages = self._receipt.detect_shortages(receipt)

        receipt_alert = self._notifier.warehouse_receipt_alert(
            po_number=po_number,
            supplier_name=booking.vessel_name or "Supplier",
            total_cases=receipt.total_cases_received,
            sku_count=len(receipt.line_items),
            lead_time_days=receipt.total_lead_time_days or 0.0,
            shortages=shortages,
        )

        return {
            "receipt": receipt,
            "shortages": shortages,
            "alert": receipt_alert,
        }

    # ── Stage 8 — Cost Reconciliation & Variance Detection ───────────────────

    def build_landed_cost(
        self,
        po_number: str,
        sku: str,
        cases: int,
        supplier_id: str,
        fob_cost_per_case: float,
        freight_booking: Optional[FreightBooking],
        duty_rate_pct: float = 0.0,
        shipment_date: Optional[str] = None,
    ) -> LandedCostBreakdown:
        """Assemble a full landed cost breakdown for this shipment."""
        return self._reconciliation.build_landed_cost(
            po_number=po_number,
            sku=sku,
            cases=cases,
            supplier_id=supplier_id,
            fob_cost_per_case=fob_cost_per_case,
            freight_booking=freight_booking,
            duty_rate_pct=duty_rate_pct,
            shipment_date=shipment_date,
        )

    def reconcile_costs(
        self,
        current_lcd: LandedCostBreakdown,
        previous_lcd: LandedCostBreakdown,
        selling_price_per_case: float = 0.0,
    ) -> dict:
        """
        Compare current vs. previous landed cost breakdown.

        Returns:
            variances   : list[CostVariance]
            alerts      : list[LogisticsAlert]
            formatted   : list[str]
        """
        variances = self._reconciliation.detect_variances(
            current=current_lcd,
            previous=previous_lcd,
            selling_price_per_case=selling_price_per_case,
        )

        alerts = []
        formatted = []
        for v in variances:
            formatted.append(
                self._reconciliation.format_variance_alert(
                    variance=v,
                    sku_name=v.sku,
                    cases=current_lcd.cases,
                )
            )
            alert = self._notifier.cost_variance_alert(
                po_number=current_lcd.po_number,
                sku_name=v.sku,
                component=v.component,
                change_pct=v.change_pct,
                per_case_impact=v.per_case_impact,
                annualized_impact=v.annualized_impact,
                cases=current_lcd.cases,
            )
            alerts.append(alert)

        return {
            "variances": variances,
            "alerts": alerts,
            "formatted": formatted,
        }

    def analyze_cost_trends(
        self,
        cost_history: list[LandedCostBreakdown],
    ) -> dict:
        """
        Analyze multi-shipment cost trends for the monthly logistics report.
        """
        return self._reconciliation.analyze_cost_trends(cost_history)

    # ── Stage 9 — Learning & Refinement ──────────────────────────────────────

    def record_lead_time(self, record: LeadTimeRecord) -> None:
        """
        Add a completed shipment lead time record to the learning engine.
        Call this after every warehouse receipt.
        """
        self._lead_time_records.append(record)

    def get_lead_time_stats(
        self,
        supplier_id: Optional[str] = None,
        month: Optional[int] = None,
        origin_country: Optional[str] = None,
    ) -> dict:
        """Retrieve lead time statistics (mean, p50, p80, p95, trend, accuracy)."""
        return self._learning.compute_lead_time_stats(
            records=self._lead_time_records,
            supplier_id=supplier_id,
            month=month,
            origin_country=origin_country,
        )

    def score_supplier(
        self,
        supplier_id: str,
        supplier_name: str = "",
        response_hours_history: Optional[list[float]] = None,
        defect_rate_pct: float = 0.0,
        return_rate_pct: float = 0.0,
        cost_history: Optional[list] = None,
    ) -> dict:
        """
        Generate a composite supplier performance score (0–100).

        Returns SupplierPerformanceScore with breakdown by dimension.
        """
        records = [
            r for r in self._lead_time_records
            if r.supplier_id == supplier_id
        ]
        score = self._learning.score_supplier(
            supplier_id=supplier_id,
            supplier_name=supplier_name or supplier_id,
            lead_time_records=records,
            response_hours=response_hours_history or [],
            cost_history=cost_history or [],
            defect_rate_pct=defect_rate_pct,
            return_rate_pct=return_rate_pct,
        )
        return score

    def build_seasonal_indices(
        self,
        sku: str,
        daily_sales_values: list[float],
    ) -> dict:
        """
        Build 52-week seasonal demand indices for a SKU.

        Args:
            daily_sales_values: list of daily sales quantities (oldest first).
        Returns {week_number: multiplier} plus data status (provisional/confident).
        """
        return self._learning.build_seasonal_indices(sku, daily_sales_values)

    def monthly_logistics_report(
        self,
        cost_history_by_sku: Optional[dict[str, list]] = None,
        supplier_scores: Optional[list] = None,
    ) -> dict:
        """
        Generate the monthly Logistics Intelligence Report.
        """
        return self._learning.monthly_logistics_report(
            lead_time_records=self._lead_time_records,
            cost_history_by_sku=cost_history_by_sku or {},
            supplier_scores=supplier_scores or [],
        )

    # ── Pipeline Status ───────────────────────────────────────────────────────

    def pipeline_status(self, cycle: ProcurementCycle) -> dict:
        """
        Return a human-readable summary of where a ProcurementCycle stands.
        """
        days_in_stage = cycle.days_in_current_stage()
        return {
            "po_number": cycle.po_number,
            "current_stage": cycle.current_stage.value,
            "days_in_current_stage": days_in_stage,
            "started_at": cycle.started_at,
            "supplier_id": cycle.supplier_id,
            "total_cases": cycle.total_cases,
            "has_freight_booking": cycle.freight_booking is not None,
            "has_warehouse_receipt": cycle.warehouse_receipt is not None,
            "cost_variances_count": len(cycle.cost_variances),
        }

    # ── Describe ──────────────────────────────────────────────────────────────

    def describe(self) -> dict:
        """Return module capabilities summary for the agent registry."""
        return {
            "module": "LogisticsModule",
            "company_id": self.company_id,
            "pipeline_stages": [
                "Stage 1 — Demand Monitoring & Reorder Intelligence",
                "Stage 2 — Purchase Order Generation",
                "Stage 3 — PO Transmission & Vendor Response Tracking",
                "Stage 4 — Freight Booking & Cost Capture",
                "Stage 5 — Ocean / Ground Transit Tracking",
                "Stage 6 — Port Arrival & Customs Clearance",
                "Stage 7 — Domestic Drayage & Warehouse Receipt",
                "Stage 8 — Cost Reconciliation & Variance Detection",
                "Stage 9 — Learning & Refinement",
            ],
            "config": {
                "service_level_target": self.config.service_level_target,
                "target_gross_margin_pct": self.config.target_gross_margin_pct,
                "port_free_time_days": self.config.port_free_time_days,
                "demurrage_rate_per_day": self.config.demurrage_rate_per_day,
                "shipments_per_year": self.config.shipments_per_year,
                "container_target_fill_pct": self.config.container_target_fill_pct,
            },
        }
