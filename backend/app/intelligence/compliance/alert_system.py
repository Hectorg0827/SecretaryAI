"""
AlertSystem — Expiration tracking, deadline generation, and daily digest.

Runs check_expirations() to produce ComplianceAlert objects.
Runs get_upcoming_deadlines() to list filing/reporting deadlines.
Generates a DailyDigest for email/dashboard consumption.
"""

from __future__ import annotations

from datetime import date, timedelta

from .entity_registry import EntityRegistry
from .models import (
    AlertPriority,
    ComplianceAlert,
    ComplianceDeadline,
    ComplianceCostEstimate,
    DailyDigest,
    DeadlineType,
)
from .state_matrix import get_state_rules


# Days-before-expiry schedules
ALERT_SCHEDULE = {
    "federal_permit":         [90, 60, 30, 14, 7],
    "state_license":          [90, 60, 30, 14, 7],
    "brand_registration":     [60, 30, 14, 7],
    "cola":                   [90, 60, 30, 14],
    "distributor_contract":   [180, 90, 60, 30],
}


def _priority(days_until: int) -> AlertPriority:
    if days_until <= 14:
        return AlertPriority.CRITICAL
    if days_until <= 60:
        return AlertPriority.WARNING
    return AlertPriority.INFO


class AlertSystem:
    """
    Scans the EntityRegistry for upcoming expirations and filing deadlines.
    Instantiate once per company session.
    """

    def __init__(self, registry: EntityRegistry) -> None:
        self._registry = registry

    # ── Expiration alerts ─────────────────────────────────────────────────────

    def check_expirations(self, lookahead_days: int = 180) -> list[ComplianceAlert]:
        """
        Return advisory alerts for recorded upcoming and overdue expirations.
        Upcoming reminders follow ALERT_SCHEDULE; overdue records remain
        critical every day until the source record is corrected or reviewed.
        These date calculations are not a legal status verification.
        """
        if lookahead_days < 0:
            raise ValueError("lookahead_days must be nonnegative")
        alerts: list[ComplianceAlert] = []

        # Federal permits
        for permit in self._registry.list_federal_permits():
            if permit.days_until_expiry is None:
                continue
            d = permit.days_until_expiry
            if d <= lookahead_days and self._on_schedule(d, "federal_permit"):
                alerts.append(ComplianceAlert(
                    alert_type="federal_permit_expiry",
                    priority=_priority(d),
                    state_code="FEDERAL",
                    item_name=f"TTB {permit.permit_type.value} Permit ({permit.permit_number})",
                    item_id=permit.id,
                    days_until=d,
                    expiry_date=permit.expiration_date,
                    message=f"TTB {permit.permit_type.value} permit {self._expiry_phrase(d)}",
                    action_required=("Verify overdue permit status and escalate for human review"
                                     if d < 0 else "Review TTB permit renewal status"),
                    renewal_url="https://www.ttb.gov/permits",
                ))

        # COLA records
        for cola in self._registry.list_colas():
            if cola.days_until_expiry is None:
                continue
            d = cola.days_until_expiry
            if d <= lookahead_days and self._on_schedule(d, "cola"):
                product = self._registry.get_product(cola.product_id)
                pname = product.name if product else cola.product_id
                alerts.append(ComplianceAlert(
                    alert_type="cola_expiry",
                    priority=_priority(d),
                    state_code="FEDERAL",
                    item_name=f"COLA for {pname} ({cola.cola_number})",
                    item_id=cola.id,
                    days_until=d,
                    expiry_date=cola.expiration_date,
                    message=f"COLA for {pname} {self._expiry_phrase(d)}",
                    action_required=("Verify overdue COLA record and escalate for human review"
                                     if d < 0 else "Review COLA status and applicable requirements"),
                    renewal_url="https://www.ttbonline.gov/colasonline",
                ))

        # State licenses
        for lic in self._registry.list_state_licenses():
            if lic.days_until_expiry is None:
                continue
            d = lic.days_until_expiry
            if d <= lookahead_days and self._on_schedule(d, "state_license"):
                state = get_state_rules(lic.state_code)
                alerts.append(ComplianceAlert(
                    alert_type="state_license_expiry",
                    priority=_priority(d),
                    state_code=lic.state_code,
                    item_name=f"{lic.state_code} {lic.license_type} License ({lic.license_number})",
                    item_id=lic.id,
                    days_until=d,
                    expiry_date=lic.expiration_date,
                    message=f"{lic.state_code} {lic.license_type} license {self._expiry_phrase(d)}",
                    action_required=("Verify overdue license status and escalate for human review"
                                     if d < 0 else f"Review {lic.state_code} license renewal with {state.regulator_name if state else 'state regulator'}"),
                    renewal_url=state.regulator_url if state else "",
                    estimated_fee=lic.annual_fee,
                ))

        # Brand registrations
        for reg in self._registry.list_brand_registrations():
            if reg.days_until_expiry is None:
                continue
            d = reg.days_until_expiry
            if d <= lookahead_days and self._on_schedule(d, "brand_registration"):
                product = self._registry.get_product(reg.product_id)
                pname = product.name if product else reg.product_id
                state = get_state_rules(reg.state_code)
                fee = 0.0
                if product and state:
                    fee = getattr(state, f"brand_reg_fee_{product.product_type.value}", 0.0)
                alerts.append(ComplianceAlert(
                    alert_type="brand_registration_expiry",
                    priority=_priority(d),
                    state_code=reg.state_code,
                    item_name=f"{reg.state_code} Brand Reg: {pname}",
                    item_id=reg.id,
                    days_until=d,
                    expiry_date=reg.expiration_date,
                    message=f"Brand registration for {pname} in {reg.state_code} {self._expiry_phrase(d)}",
                    action_required=("Verify overdue registration status and escalate for human review"
                                     if d < 0 else f"Review registration renewal with {state.regulator_name if state else reg.state_code + ' regulator'}"),
                    renewal_url=state.regulator_url if state else "",
                    estimated_fee=fee,
                ))

        # Distributor contracts
        for dist in self._registry.list_distributors():
            if dist.days_until_contract_end is None:
                continue
            d = dist.days_until_contract_end
            if d <= lookahead_days and self._on_schedule(d, "distributor_contract"):
                alerts.append(ComplianceAlert(
                    alert_type="distributor_contract_expiry",
                    priority=_priority(d),
                    state_code=dist.state_code,
                    item_name=f"{dist.state_code} Distributor: {dist.distributor_name}",
                    item_id=dist.id,
                    days_until=d,
                    expiry_date=dist.contract_end_date,
                    message=(
                        f"Distribution agreement with {dist.distributor_name} in "
                        f"{dist.state_code} {self._expiry_phrase(d)}"
                    ),
                    action_required=(
                        "Initiate renewal negotiation. Review franchise law implications "
                        "before any modification or termination."
                    ),
                ))

        alerts.sort(key=lambda a: (a.days_until, a.state_code))
        return alerts

    # ── Deadline tracker ──────────────────────────────────────────────────────

    def get_upcoming_deadlines(self, days_ahead: int = 30) -> list[ComplianceDeadline]:
        """
        Return all compliance filing/reporting deadlines in the next days_ahead days.
        """
        deadlines: list[ComplianceDeadline] = []
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)

        active_states = self._registry.states_with_active_license()
        for sc in active_states:
            state = get_state_rules(sc)
            if state is None:
                continue

            # Excise tax reporting
            next_report = self._next_report_date(today, state.reporting_frequency)
            if today <= next_report <= cutoff:
                deadlines.append(ComplianceDeadline(
                    deadline_type=DeadlineType.TAX_REPORT,
                    state_code=sc,
                    due_date=next_report,
                    frequency=state.reporting_frequency,
                    action=f"File {state.reporting_frequency} state excise tax report for {sc}",
                    notes=f"Regulator: {state.regulator_name}",
                ))

            # Price posting (if required)
            if (state.price_posting_required_wine or state.price_posting_required_spirits):
                next_post = self._next_price_posting_date(today, state.post_and_hold_days)
                if today <= next_post <= cutoff:
                    deadlines.append(ComplianceDeadline(
                        deadline_type=DeadlineType.PRICE_POSTING,
                        state_code=sc,
                        due_date=next_post,
                        frequency="monthly",
                        action=f"Submit updated price list to {sc} ({state.regulator_name})",
                        notes=f"Post-and-hold period: {state.post_and_hold_days} days",
                    ))

        deadlines.sort(key=lambda d: d.due_date)
        return deadlines

    # ── Daily digest ──────────────────────────────────────────────────────────

    def generate_daily_digest(self) -> DailyDigest:
        """
        Build the daily compliance digest for email/dashboard rendering.
        """
        all_alerts = self.check_expirations(lookahead_days=180)
        deadlines_30 = self.get_upcoming_deadlines(days_ahead=30)

        critical = [a for a in all_alerts if a.priority == AlertPriority.CRITICAL]
        warnings = [a for a in all_alerts if a.priority == AlertPriority.WARNING]

        # State-level health status
        state_status: dict[str, str] = {}
        active_states = set(self._registry.states_with_active_license())
        # A state with only expired/inactive licenses must remain visible as a
        # critical review item, not disappear from the daily summary.
        tracked_states = active_states | {
            lic.state_code for lic in self._registry.list_state_licenses()
            if lic.state_code
        }
        for sc in sorted(tracked_states):
            state_alerts = [a for a in all_alerts if a.state_code == sc]
            if sc not in active_states or any(a.priority == AlertPriority.CRITICAL for a in state_alerts):
                state_status[sc] = "critical"
            elif any(a.priority == AlertPriority.WARNING for a in state_alerts):
                state_status[sc] = "warning"
            else:
                state_status[sc] = "ok"

        return DailyDigest(
            date=date.today(),
            critical_alerts=critical,
            warning_alerts=warnings,
            upcoming_deadlines=deadlines_30,
            state_status=state_status,
            cost_estimate_q=ComplianceCostEstimate(),  # populated by ComplianceModule
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _expiry_phrase(days_until: int) -> str:
        if days_until < 0:
            days_overdue = -days_until
            return f"expired {days_overdue} day{'s' if days_overdue != 1 else ''} ago"
        if days_until == 0:
            return "expires today"
        return f"expires in {days_until} days"

    @staticmethod
    def _on_schedule(days_until: int, entity_type: str) -> bool:
        """
        Return True if days_until is close to any scheduled alert milestone (±3 days).
        Also fires for any day ≤ 7 to ensure nothing is missed.
        """
        if days_until <= 7:
            return True
        schedule = ALERT_SCHEDULE.get(entity_type, [30, 14, 7])
        return any(abs(days_until - threshold) <= 3 for threshold in schedule)

    @staticmethod
    def _next_report_date(today: date, frequency: str) -> date:
        """Return the next excise report due date based on frequency."""
        if frequency == "monthly":
            # Due on the 15th of next month for prior month activity
            if today.month == 12:
                return date(today.year + 1, 1, 15)
            return date(today.year, today.month + 1, 15)
        elif frequency == "quarterly":
            # Due mid-month following the quarter
            quarter_end_months = {1: 3, 2: 3, 3: 3, 4: 6, 5: 6, 6: 6,
                                  7: 9, 8: 9, 9: 9, 10: 12, 11: 12, 12: 12}
            end_month = quarter_end_months[today.month]
            if end_month < today.month:
                return date(today.year + 1, end_month % 12 + 1, 15)
            return date(today.year, end_month + 1 if end_month < 12 else 1, 15)
        else:  # annual
            return date(today.year + 1, 1, 31)

    @staticmethod
    def _next_price_posting_date(today: date, post_and_hold_days: int) -> date:
        """Price postings typically due on the 1st of each month."""
        if today.month == 12:
            return date(today.year + 1, 1, 1)
        return date(today.year, today.month + 1, 1)
