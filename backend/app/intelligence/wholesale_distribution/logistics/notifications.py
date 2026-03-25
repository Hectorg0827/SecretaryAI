"""
Email Notification Routing System for the Logistics & Procurement Pipeline.

Every alert type has a configurable recipient table:
  - who gets an immediate email
  - who sees it in the daily digest only
  - who sees it in the Logistics Manager UI only

Alert types (mapped from pipeline events):
  reorder_red          → immediate email: logistics_manager, owner
  po_confirmed         → email: logistics_manager
  po_follow_up_needed  → email: logistics_manager
  shipment_delayed     → email: logistics_manager, sales_manager, owner (if $ threshold)
  customs_hold         → immediate email: logistics_manager, owner
  warehouse_received   → email: logistics_manager, sales_team
  cost_variance        → email: logistics_manager, owner
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AlertType(str, Enum):
    REORDER_RED = "reorder_red"
    REORDER_YELLOW = "reorder_yellow"
    PO_SENT = "po_sent"
    PO_CONFIRMED = "po_confirmed"
    PO_PARTIALLY_CONFIRMED = "po_partially_confirmed"
    PO_REJECTED = "po_rejected"
    PO_FOLLOW_UP_NEEDED = "po_follow_up_needed"
    PO_NO_RESPONSE_CRITICAL = "po_no_response_critical"
    SHIPMENT_DELAYED = "shipment_delayed"
    SHIPMENT_ON_TRACK = "shipment_on_track"
    CUSTOMS_HOLD = "customs_hold"
    CUSTOMS_DOC_ISSUE = "customs_doc_issue"
    DEMURRAGE_RISK = "demurrage_risk"
    WAREHOUSE_RECEIVED = "warehouse_received"
    COST_VARIANCE = "cost_variance"
    COST_VARIANCE_CRITICAL = "cost_variance_critical"
    SHORTAGE_ON_RECEIPT = "shortage_on_receipt"


class DeliveryChannel(str, Enum):
    EMAIL_IMMEDIATE = "email_immediate"
    DAILY_DIGEST = "daily_digest"
    UI_ONLY = "ui_only"


@dataclass
class RecipientConfig:
    """Who receives which alert type, and how."""
    role: str                            # logistics_manager | owner | sales_manager | sales_team
    channel: DeliveryChannel
    financial_threshold_usd: float = 0.0  # only send if impact ≥ this amount (0 = always)


@dataclass
class LogisticsAlert:
    """A structured alert ready for delivery."""
    alert_type: AlertType
    severity: str                        # info | warning | alert | critical
    subject: str
    body: str
    context: dict[str, Any] = field(default_factory=dict)
    financial_impact_usd: float = 0.0
    recipients: list[str] = field(default_factory=list)   # email addresses
    digest_recipients: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "alert_type": self.alert_type.value,
            "severity": self.severity,
            "subject": self.subject,
            "body": self.body,
            "financial_impact_usd": round(self.financial_impact_usd, 2),
            "recipients": self.recipients,
            "digest_recipients": self.digest_recipients,
        }


# ─── Default Routing Table ────────────────────────────────────────────────────

DEFAULT_ROUTING: dict[AlertType, list[RecipientConfig]] = {
    AlertType.REORDER_RED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.REORDER_YELLOW: [
        RecipientConfig("logistics_manager", DeliveryChannel.DAILY_DIGEST),
    ],
    AlertType.PO_SENT: [
        RecipientConfig("logistics_manager", DeliveryChannel.UI_ONLY),
    ],
    AlertType.PO_CONFIRMED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.PO_PARTIALLY_CONFIRMED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.DAILY_DIGEST),
    ],
    AlertType.PO_REJECTED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.PO_FOLLOW_UP_NEEDED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.PO_NO_RESPONSE_CRITICAL: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.SHIPMENT_DELAYED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("sales_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE, financial_threshold_usd=1_000),
    ],
    AlertType.SHIPMENT_ON_TRACK: [
        RecipientConfig("logistics_manager", DeliveryChannel.UI_ONLY),
    ],
    AlertType.CUSTOMS_HOLD: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.CUSTOMS_DOC_ISSUE: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.DEMURRAGE_RISK: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.DAILY_DIGEST),
    ],
    AlertType.WAREHOUSE_RECEIVED: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("sales_team", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.COST_VARIANCE: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.COST_VARIANCE_CRITICAL: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.EMAIL_IMMEDIATE),
    ],
    AlertType.SHORTAGE_ON_RECEIPT: [
        RecipientConfig("logistics_manager", DeliveryChannel.EMAIL_IMMEDIATE),
        RecipientConfig("owner", DeliveryChannel.DAILY_DIGEST),
    ],
}


# ─── Notification Router ──────────────────────────────────────────────────────


class NotificationRouter:
    """
    Resolves which team members receive each alert type and via which channel.
    Builds ready-to-send LogisticsAlert objects with subject/body pre-composed.
    """

    def __init__(
        self,
        company_id: str,
        role_email_map: dict[str, list[str]],
        # {"logistics_manager": ["lm@co.com"], "owner": ["hector@co.com"], ...}
        routing_overrides: Optional[dict[AlertType, list[RecipientConfig]]] = None,
    ):
        self.company_id = company_id
        self.role_email_map = role_email_map
        self.routing = {**DEFAULT_ROUTING, **(routing_overrides or {})}

    def build_alert(
        self,
        alert_type: AlertType,
        context: dict[str, Any],
        financial_impact_usd: float = 0.0,
    ) -> LogisticsAlert:
        """Build a fully-resolved alert with recipient lists and composed body."""
        from datetime import datetime, timezone
        subject, body, severity = self._compose(alert_type, context, financial_impact_usd)

        immediate_recipients: list[str] = []
        digest_recipients: list[str] = []

        for cfg in self.routing.get(alert_type, []):
            if cfg.financial_threshold_usd > 0 and financial_impact_usd < cfg.financial_threshold_usd:
                # Below this role's threshold — downgrade to digest
                emails = self.role_email_map.get(cfg.role, [])
                digest_recipients.extend(emails)
                continue

            emails = self.role_email_map.get(cfg.role, [])
            if cfg.channel == DeliveryChannel.EMAIL_IMMEDIATE:
                immediate_recipients.extend(emails)
            elif cfg.channel == DeliveryChannel.DAILY_DIGEST:
                digest_recipients.extend(emails)
            # UI_ONLY → no email

        return LogisticsAlert(
            alert_type=alert_type,
            severity=severity,
            subject=subject,
            body=body,
            context=context,
            financial_impact_usd=financial_impact_usd,
            recipients=list(dict.fromkeys(immediate_recipients)),   # deduplicate
            digest_recipients=list(dict.fromkeys(digest_recipients)),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── Pre-composed alert factories ─────────────────────────────────────────

    def reorder_alert(
        self,
        sku_name: str,
        sku: str,
        days_of_supply: float,
        lead_time_days: float,
        urgency: str,
        estimated_cost: float = 0.0,
    ) -> LogisticsAlert:
        alert_type = AlertType.REORDER_RED if urgency == "red" else AlertType.REORDER_YELLOW
        ctx = {
            "sku": sku, "sku_name": sku_name,
            "days_of_supply": days_of_supply, "lead_time_days": lead_time_days,
        }
        return self.build_alert(alert_type, ctx, financial_impact_usd=estimated_cost)

    def po_confirmed_alert(
        self,
        po_number: str,
        supplier_name: str,
        ship_date: Optional[str],
        total_value: float,
    ) -> LogisticsAlert:
        ctx = {
            "po_number": po_number,
            "supplier_name": supplier_name,
            "ship_date": ship_date,
            "total_value": total_value,
        }
        return self.build_alert(AlertType.PO_CONFIRMED, ctx, financial_impact_usd=total_value)

    def shipment_delay_alert(
        self,
        po_number: str,
        supplier_name: str,
        original_eta: str,
        updated_eta: str,
        delay_days: int,
        at_risk_skus: list[str],
        estimated_revenue_impact: float,
    ) -> LogisticsAlert:
        ctx = {
            "po_number": po_number,
            "supplier_name": supplier_name,
            "original_eta": original_eta,
            "updated_eta": updated_eta,
            "delay_days": delay_days,
            "at_risk_skus": at_risk_skus,
        }
        return self.build_alert(
            AlertType.SHIPMENT_DELAYED, ctx,
            financial_impact_usd=estimated_revenue_impact,
        )

    def cost_variance_alert(
        self,
        po_number: str,
        sku_name: str,
        component: str,
        change_pct: float,
        per_case_impact: float,
        annualized_impact: float,
        cases: int,
    ) -> LogisticsAlert:
        alert_type = (
            AlertType.COST_VARIANCE_CRITICAL
            if abs(annualized_impact) > 10_000
            else AlertType.COST_VARIANCE
        )
        ctx = {
            "po_number": po_number, "sku_name": sku_name,
            "component": component, "change_pct": change_pct,
            "per_case_impact": per_case_impact,
            "annualized_impact": annualized_impact, "cases": cases,
        }
        return self.build_alert(alert_type, ctx, financial_impact_usd=annualized_impact)

    def warehouse_receipt_alert(
        self,
        po_number: str,
        supplier_name: str,
        total_cases: int,
        sku_count: int,
        lead_time_days: float,
        shortages: list[dict],
    ) -> LogisticsAlert:
        ctx = {
            "po_number": po_number, "supplier_name": supplier_name,
            "total_cases": total_cases, "sku_count": sku_count,
            "lead_time_days": lead_time_days, "shortages": shortages,
        }
        alert_type = AlertType.SHORTAGE_ON_RECEIPT if shortages else AlertType.WAREHOUSE_RECEIVED
        return self.build_alert(alert_type, ctx)

    # ── Message composer ─────────────────────────────────────────────────────

    @staticmethod
    def _compose(
        alert_type: AlertType,
        ctx: dict,
        financial_impact: float,
    ) -> tuple[str, str, str]:
        """Returns (subject, body, severity)."""

        if alert_type == AlertType.REORDER_RED:
            sku_name = ctx.get("sku_name", ctx.get("sku", "Unknown SKU"))
            days = ctx.get("days_of_supply", 0)
            lt = ctx.get("lead_time_days", 0)
            return (
                f"🔴 REORDER NOW — {sku_name} stockout risk",
                (
                    f"{sku_name} has {days:.0f} days of supply remaining.\n"
                    f"Overseas lead time: {lt:.0f} days.\n"
                    f"Reorder required IMMEDIATELY to avoid a stockout.\n\n"
                    f"A draft PO is ready in the Logistics Manager for one-click approval."
                ),
                "critical",
            )

        if alert_type == AlertType.REORDER_YELLOW:
            sku_name = ctx.get("sku_name", ctx.get("sku", "Unknown SKU"))
            days = ctx.get("days_of_supply", 0)
            return (
                f"⚠️ Reorder this week — {sku_name}",
                (
                    f"{sku_name} has {days:.0f} days of supply remaining.\n"
                    f"Reorder this week to maintain stock levels.\n\n"
                    f"See the Reorder Queue in Logistics Manager for details."
                ),
                "warning",
            )

        if alert_type == AlertType.PO_CONFIRMED:
            return (
                f"✅ PO Confirmed — {ctx.get('supplier_name')} ({ctx.get('po_number')})",
                (
                    f"{ctx.get('supplier_name')} confirmed {ctx.get('po_number')}.\n"
                    f"ETA ship date: {ctx.get('ship_date', 'TBD')}.\n"
                    f"Order value: ${ctx.get('total_value', 0):,.2f}."
                ),
                "info",
            )

        if alert_type == AlertType.PO_REJECTED:
            return (
                f"❌ PO REJECTED — {ctx.get('supplier_name')} ({ctx.get('po_number')})",
                (
                    f"{ctx.get('supplier_name')} has rejected {ctx.get('po_number')}.\n"
                    f"Immediate action required — contact supplier and investigate alternatives."
                ),
                "critical",
            )

        if alert_type == AlertType.PO_FOLLOW_UP_NEEDED:
            return (
                f"📧 Follow-up needed — {ctx.get('po_number')} to {ctx.get('supplier_name', '')}",
                (
                    f"{ctx.get('po_number')} sent to {ctx.get('supplier_name', 'supplier')} "
                    f"{ctx.get('hours_since_sent', 0):.0f} hours ago with no response.\n"
                    f"A follow-up email has been drafted — please review and send."
                ),
                "warning",
            )

        if alert_type in (AlertType.SHIPMENT_DELAYED,):
            delay = ctx.get("delay_days", 0)
            skus = ctx.get("at_risk_skus", [])
            sku_list = ", ".join(skus[:3]) + ("..." if len(skus) > 3 else "")
            return (
                f"⚠️ Shipment delayed {delay} days — {ctx.get('supplier_name')} ({ctx.get('po_number')})",
                (
                    f"Container from {ctx.get('supplier_name')} now ETA {ctx.get('updated_eta')} "
                    f"(was {ctx.get('original_eta')}) — {delay} day delay.\n"
                    f"SKUs at stockout risk: {sku_list or 'none'}.\n"
                    f"Estimated revenue impact: ${financial_impact:,.0f}.\n\n"
                    f"See mitigation options in the Logistics Manager."
                ),
                "alert" if financial_impact < 5_000 else "critical",
            )

        if alert_type in (AlertType.CUSTOMS_HOLD, AlertType.CUSTOMS_DOC_ISSUE):
            return (
                f"🚨 Customs Hold — {ctx.get('po_number', '')} [{ctx.get('hold_reason', '')}]",
                (
                    f"Container placed on customs hold.\n"
                    f"Reason: {ctx.get('hold_reason', 'Under investigation')}.\n"
                    f"Demurrage accruing at ${ctx.get('demurrage_rate', 150)}/day.\n"
                    f"Contact customs broker immediately to resolve.\n\n"
                    f"Every day of delay costs money and risks stockouts."
                ),
                "critical",
            )

        if alert_type == AlertType.DEMURRAGE_RISK:
            return (
                f"⏰ Demurrage risk — {ctx.get('po_number', '')} ({ctx.get('days_at_port', 0)} days at port)",
                (
                    f"Container has been at port {ctx.get('days_at_port', 0)} days.\n"
                    f"Free time remaining: {ctx.get('free_time_remaining_days', 0)} day(s).\n"
                    f"Current demurrage: ${ctx.get('current_demurrage', 0):,.0f}.\n"
                    f"Expedite clearance to minimize cost."
                ),
                "warning",
            )

        if alert_type == AlertType.WAREHOUSE_RECEIVED:
            return (
                f"📦 Received — {ctx.get('supplier_name')} {ctx.get('po_number')} "
                f"({ctx.get('total_cases', 0)} cases)",
                (
                    f"Container from {ctx.get('supplier_name')} received at warehouse.\n"
                    f"{ctx.get('total_cases', 0)} cases across {ctx.get('sku_count', 0)} SKUs "
                    f"now in inventory.\n"
                    f"Total lead time: {ctx.get('lead_time_days', 0):.0f} days."
                ),
                "info",
            )

        if alert_type == AlertType.SHORTAGE_ON_RECEIPT:
            shortages = ctx.get("shortages", [])
            shortage_lines = "\n".join(
                f"  • {s['sku']}: ordered {s['qty_ordered']}, received {s['qty_received']} "
                f"(short {s['shortage']})"
                for s in shortages[:5]
            )
            return (
                f"⚠️ Shortages on receipt — {ctx.get('po_number', '')}",
                (
                    f"Warehouse receipt for {ctx.get('po_number', '')} shows shortages:\n"
                    f"{shortage_lines}\n\n"
                    f"File a shortage claim with {ctx.get('supplier_name', 'supplier')} "
                    f"and update inventory."
                ),
                "warning",
            )

        if alert_type in (AlertType.COST_VARIANCE, AlertType.COST_VARIANCE_CRITICAL):
            return (
                f"{'🚨' if alert_type == AlertType.COST_VARIANCE_CRITICAL else '💰'} "
                f"Cost variance — {ctx.get('sku_name', '')} [{ctx.get('component', '')}]",
                (
                    f"{ctx.get('component', '').replace('_', ' ').title()} changed "
                    f"{ctx.get('change_pct', 0):+.1f}% on {ctx.get('sku_name', '')}.\n"
                    f"Per-case impact: ${abs(ctx.get('per_case_impact', 0)):.4f}.\n"
                    f"Annualized impact: ${abs(ctx.get('annualized_impact', 0)):,.0f}.\n\n"
                    f"See full cost breakdown in the Logistics Manager cost variance panel."
                ),
                "critical" if alert_type == AlertType.COST_VARIANCE_CRITICAL else "alert",
            )

        # Default
        return (
            f"Logistics alert: {alert_type.value}",
            str(ctx),
            "info",
        )
