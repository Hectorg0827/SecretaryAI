"""
Proactive logistics monitoring.

``LogisticsMonitor.scan(as_of)`` reads the ledger and returns prioritized
alerts, each with recommended actions and — where a message to someone outside
the company would help — a reviewable *draft* (``send: False``).  The monitor
never sends, books, files or posts anything.

Detections are computed only from verified facts, except the explicit
``UNVERIFIED_REPORT`` alert, which surfaces emails / AI readings that a person
should confirm or reject.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .journey import (
    REQUIRED_CUSTOMS_DOCUMENTS, JourneyStage, Shipment, ShipmentLedger,
    _days_between, _parse_date,
)
from .landed_cost import landed_cost


SEVERITY_WEIGHT = {"critical": 100, "alert": 70, "warning": 40, "info": 10}


@dataclass
class MonitorConfig:
    delay_threshold_days: int = 2
    supplier_confirmation_sla_hours_overseas: int = 72
    supplier_confirmation_sla_hours_domestic: int = 24
    customs_docs_lead_days: int = 7
    lfd_warning_days: int = 2
    default_demurrage_rate_per_day: float = 150.0
    default_detention_free_days: int = 4
    default_detention_rate_per_day: float = 100.0
    port_to_warehouse_days: int = 3
    warehouse_to_customer_days: int = 2
    freight_variance_pct: float = 5.0
    dock_capacity: int = 1                 # concurrent appointments per dock
    supplier_min_on_time_pct: float = 80.0
    supplier_max_damage_pct: float = 1.0
    supplier_on_time_grace_days: int = 2


@dataclass
class LogisticsAlert:
    alert_id: str
    code: str
    severity: str
    title: str
    message: str
    shipment_id: str = ""
    stage: str = ""
    priority: int = 0
    recommended_actions: list = field(default_factory=list)
    draft: Optional[dict] = None
    financial_exposure_usd: float = 0.0
    customer_ids: list = field(default_factory=list)
    supplier_id: str = ""
    contains_commercial: bool = False      # $ amounts / supplier performance → finance-only detail

    def to_dict(self) -> dict:
        return asdict(self)


class LogisticsMonitor:
    def __init__(self, ledger: ShipmentLedger, config: Optional[MonitorConfig] = None):
        self.ledger = ledger
        self.cfg = config or MonitorConfig()

    def scan(self, as_of: Optional[date] = None) -> list[LogisticsAlert]:
        today = as_of or date.today()
        alerts: list[LogisticsAlert] = []
        active = [s for s in self.ledger.shipments() if not s.has("financially_reconciled")]
        for s in active:
            for check in (
                self._delays, self._late_supplier_confirmation, self._missing_bol,
                self._missing_customs_docs, self._customs_hold, self._port_storage,
                self._detention, self._receipt_exceptions, self._customer_commitments,
                self._freight_variance, self._overdue_po, self._unverified_reports,
                self._sequence_exceptions,
            ):
                alerts.extend(check(s, today))
        alerts.extend(self._appointment_conflicts(active))
        alerts.extend(self._supplier_performance(self.ledger.shipments()))

        for a in alerts:
            a.priority = int(SEVERITY_WEIGHT.get(a.severity, 0)
                             + min(30, a.financial_exposure_usd / 100)
                             + 10 * len(a.customer_ids))
        alerts.sort(key=lambda a: (-a.priority, a.shipment_id, a.code))
        return alerts

    # ── individual detections ────────────────────────────────────────────────

    def _delays(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        out = []
        delay = s.eta_delay_days()
        cur = s.current_eta()
        if delay >= self.cfg.delay_threshold_days and not s.has("vessel_arrived"):
            out.append(_alert(
                "SHIPMENT_DELAYED", "alert", s,
                f"{_ref(s)} delayed {delay} day(s)",
                f"Verified ETA moved from {s.baseline_eta().eta} to {cur.eta} "
                f"(source: {cur.source.value}).",
                ["Check which customer orders depend on this shipment",
                 "Ask the forwarder for a recovery plan / alternative routing"],
                stage=JourneyStage.IN_TRANSIT,
                customer_ids=_open_customers(s)))
        # Expected milestone dates that passed with no verified actual.
        for code, exp in s.expected_dates.items():
            d = _parse_date(exp)
            if d and d < today and not s.has(code) and code in ("vessel_departed", "cargo_ready",
                                                                  "vessel_arrived", "delivered"):
                late = (today - d).days
                if late >= self.cfg.delay_threshold_days:
                    out.append(_alert(
                        "MILESTONE_OVERDUE", "warning", s,
                        f"{_ref(s)}: '{code.replace('_', ' ')}' overdue {late} day(s)",
                        f"Expected by {exp}; no verified confirmation yet.",
                        [f"Request a status update from the {s.responsible_party(_stage_of(code))}"],
                        stage=_stage_of(code), key=code))
        return out

    def _late_supplier_confirmation(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        if not s.has("po_sent") or s.has("supplier_confirmed") or s.cancelled:
            return []
        sent = _parse_dt(s.milestone_date("po_sent"))
        if not sent:
            return []
        sla = (self.cfg.supplier_confirmation_sla_hours_overseas if s.is_import
               else self.cfg.supplier_confirmation_sla_hours_domestic)
        hours = (datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - sent).total_seconds() / 3600
        if hours < sla:
            return []
        pending = [e for e in s.pending_evidence() if e.code == "supplier_confirmed"]
        msg = f"PO {', '.join(s.po_numbers)} sent {int(hours)}h ago; no verified supplier confirmation (SLA {sla}h)."
        if pending:
            msg += f" An unverified confirmation exists ({pending[0].source.value}) — review it."
        return [_alert(
            "SUPPLIER_CONFIRMATION_LATE", "warning", s, f"{_ref(s)}: supplier confirmation late", msg,
            ["Review any unverified confirmation in the evidence queue",
             "Send the follow-up draft to the supplier after review"],
            stage=JourneyStage.SUPPLIER_CONFIRMATION, supplier_id=s.supplier_id,
            draft=_draft("supplier", f"Follow-up: PO {', '.join(s.po_numbers)} confirmation",
                         f"Hello {s.supplier_name or ''},\n\nWe have not yet received confirmation of "
                         f"PO {', '.join(s.po_numbers)}. Please confirm quantities, price and ready date.\n"))]

    def _missing_bol(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        if not s.is_import or "bill_of_lading" in s.document_types():
            return []
        departed = s.has("vessel_departed")
        etd = _parse_date(s.expected_dates.get("vessel_departed"))
        if not departed and not (etd and etd < today):
            return []
        return [_alert(
            "MISSING_BOL", "alert", s, f"{_ref(s)}: bill of lading missing",
            "Cargo has departed (or should have) but no B/L is on file.",
            ["Request the B/L copy from the forwarder", "Confirm B/L release type (original/telex/seawaybill)"],
            stage=JourneyStage.IN_TRANSIT,
            draft=_draft("forwarder", f"B/L request — {_ref(s)}",
                         f"Please send the bill of lading for {_ref(s)} "
                         f"(container {', '.join(s.container_numbers) or 'TBC'}).\n"))]

    def _missing_customs_docs(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        if not s.is_import or s.has("customs_released"):
            return []
        missing = s.missing_documents(REQUIRED_CUSTOMS_DOCUMENTS)
        if not missing:
            return []
        cur = s.current_eta()
        days_to_eta = _days_between(today, cur.eta) if cur else None
        arrived = s.has("vessel_arrived")
        if not arrived and (days_to_eta is None or days_to_eta > self.cfg.customs_docs_lead_days):
            return []
        sev = "critical" if arrived else "alert"
        return [_alert(
            "MISSING_CUSTOMS_DOCUMENTS", sev, s, f"{_ref(s)}: customs documents missing",
            f"Missing {', '.join(missing)}; "
            + ("cargo has arrived." if arrived else f"arrives in {days_to_eta} day(s)."),
            ["Request missing documents from the supplier", "Share received documents with the broker"],
            stage=JourneyStage.CUSTOMS, key=",".join(missing),
            draft=_draft("supplier", f"Documents needed for customs — {_ref(s)}",
                         f"Please send: {', '.join(d.replace('_', ' ') for d in missing)} for "
                         f"PO {', '.join(s.po_numbers)}.\n"))]

    def _customs_hold(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        out = []
        for e in s.open_exceptions():
            if e.code == "CUSTOMS_HOLD":
                out.append(_alert(
                    "CUSTOMS_HOLD", "critical", s, f"{_ref(s)}: customs hold", e.message,
                    ["Ask the broker for the hold reason and required documents",
                     "Estimate added demurrage while held"],
                    stage=JourneyStage.CUSTOMS, customer_ids=_open_customers(s), key=e.exception_id,
                    draft=_draft("broker", f"Customs hold — {_ref(s)}",
                                 f"Please advise what is needed to release {_ref(s)} and the expected timeline.\n")))
        return out

    def _port_storage(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        if not s.is_import or s.has("gate_out_port"):
            return []
        lfd = _parse_date(s.port_terms.get("last_free_day"))
        if not lfd:
            if s.has("vessel_arrived"):
                return [_alert("LFD_UNKNOWN", "warning", s, f"{_ref(s)}: last free day unknown",
                               "Cargo has arrived but no verified last free day is on file.",
                               ["Get the last free day from the terminal / arrival notice"],
                               stage=JourneyStage.PORT_ARRIVAL)]
            return []
        rate = float(s.port_terms.get("demurrage_rate_per_day") or self.cfg.default_demurrage_rate_per_day)
        days_left = (lfd - today).days
        if days_left < 0:
            accrued = -days_left * rate
            return [_alert(
                "DEMURRAGE_ACCRUING", "critical", s, f"{_ref(s)}: demurrage accruing",
                f"Last free day was {lfd.isoformat()} ({-days_left} day(s) ago).",
                ["Schedule pickup immediately", "Check whether a customs hold is the cause (dispute if so)"],
                stage=JourneyStage.PORT_ARRIVAL, exposure=accrued, customer_ids=_open_customers(s),
                commercial=True,
                draft=_draft("drayage", f"Urgent pickup — {_ref(s)}",
                             f"Please pick up container {', '.join(s.container_numbers)} at the first "
                             "available slot; free time has expired.\n"))]
        if days_left <= self.cfg.lfd_warning_days:
            blocked = not s.has("customs_released")
            return [_alert(
                "DEMURRAGE_RISK", "alert", s, f"{_ref(s)}: free time ends in {days_left} day(s)",
                f"Last free day {lfd.isoformat()}"
                + ("; cargo is NOT yet released by customs." if blocked else "."),
                (["Push the broker for release"] if blocked else [])
                + ["Book a drayage pickup before the last free day"],
                stage=JourneyStage.PORT_ARRIVAL, exposure=rate * 3, commercial=True)]
        return []

    def _detention(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        if not s.has("gate_out_port") or s.has("empty_returned"):
            return []
        out_d = _parse_date(s.milestone_date("gate_out_port"))
        if not out_d:
            return []
        free = int(s.port_terms.get("detention_free_days") or self.cfg.default_detention_free_days)
        rate = float(s.port_terms.get("detention_rate_per_day") or self.cfg.default_detention_rate_per_day)
        days_out = (today - out_d).days
        if days_out <= free - 1:
            return []
        over = max(0, days_out - free)
        return [_alert(
            "DETENTION_RISK" if over == 0 else "DETENTION_ACCRUING", "alert" if over else "warning", s,
            f"{_ref(s)}: empty container not returned ({days_out} day(s) out)",
            f"Detention free time {free} day(s); {over} chargeable day(s) so far.",
            ["Unload and return the empty container"], stage=JourneyStage.PORT_ARRIVAL,
            exposure=over * rate, commercial=True)]

    def _receipt_exceptions(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        out = []
        for e in s.open_exceptions():
            if e.code in ("RECEIPT_DAMAGE", "RECEIPT_SHORTAGE", "RECEIPT_OVERAGE"):
                out.append(_alert(
                    e.code, "alert" if e.code != "RECEIPT_OVERAGE" else "warning", s,
                    f"{_ref(s)}: {e.code.replace('RECEIPT_', '').lower()} at receiving", e.message,
                    ["Review the reconciliation and approve the inventory postings",
                     "Review the claim draft and supporting documents"],
                    stage=JourneyStage.INVENTORY_RECONCILIATION, key=e.exception_id,
                    customer_ids=_open_customers(s)))
        return out

    def _customer_commitments(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        out = []
        projected = self._projected_customer_date(s, today)
        for a in s.allocations:
            if a.status in ("delivered", "cancelled") or not a.promised_date:
                continue
            promised = _parse_date(a.promised_date)
            if not promised:
                continue
            if projected is None:
                if promised <= today + timedelta(days=self.cfg.warehouse_to_customer_days):
                    out.append(_alert(
                        "CUSTOMER_COMMITMENT_AT_RISK", "warning", s,
                        f"Order {a.order_id}: no verified ETA to support promise {a.promised_date}",
                        "The supporting shipment has no verified ETA.",
                        ["Confirm availability before re-confirming the date to the customer"],
                        stage=JourneyStage.CUSTOMER_ALLOCATION, customer_ids=[a.customer_id], key=a.order_id))
                continue
            if projected > promised:
                slip = (projected - promised).days
                out.append(_alert(
                    "CUSTOMER_COMMITMENT_AT_RISK", "alert" if slip > 2 else "warning", s,
                    f"Order {a.order_id} likely late by {slip} day(s)",
                    f"Promised {a.promised_date}; projected delivery {projected.isoformat()} "
                    f"(verified ETA + handling).",
                    ["Offer the customer a revised date or partial from stock",
                     "Check if another shipment / warehouse stock can cover"],
                    stage=JourneyStage.DELIVERY, customer_ids=[a.customer_id], key=a.order_id,
                    draft=_draft("customer", f"Update on your order {a.order_id}",
                                 f"We expect your order {a.order_id} to arrive around {projected.isoformat()} "
                                 f"rather than {a.promised_date}. We apologise and will confirm shortly.\n",
                                 to_id=a.customer_id)))
        return out

    def _projected_customer_date(self, s: Shipment, today: date) -> Optional[date]:
        if s.has("warehouse_received") or s.has("arrived_at_warehouse"):
            return today + timedelta(days=self.cfg.warehouse_to_customer_days)
        cur = s.current_eta()
        if not cur:
            return None
        base = today if s.has("vessel_arrived") else max(_parse_date(cur.eta) or today, today)
        port = self.cfg.port_to_warehouse_days if (s.is_import and cur.location != "warehouse") else 0
        return base + timedelta(days=port + self.cfg.warehouse_to_customer_days)

    def _freight_variance(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        lc = landed_cost(s)
        pct = lc.get("freight_variance_pct")
        if pct is None or abs(pct) < self.cfg.freight_variance_pct:
            return []
        diff = lc["freight_invoiced_usd"] - lc["freight_quote_usd"]
        return [_alert(
            "FREIGHT_COST_VARIANCE", "alert" if pct > 0 else "info", s,
            f"{_ref(s)}: freight {pct:+.1f}% vs quote",
            f"Invoiced ${lc['freight_invoiced_usd']:,.2f} vs quoted ${lc['freight_quote_usd']:,.2f}.",
            ["Ask the forwarder to itemise the difference before approving payment"],
            stage=JourneyStage.CLAIMS_RECONCILIATION, exposure=max(0.0, diff), commercial=True)]

    def _overdue_po(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        rsd = _parse_date(s.requested_ship_date)
        if not rsd or s.cancelled:
            return []
        # Any verified progress at/after readiness means the PO is not "overdue".
        if s.milestones and s.current_stage.order >= JourneyStage.PRODUCTION_READINESS.order:
            return []
        late = (today - rsd).days
        if late <= 0:
            return []
        return [_alert(
            "PO_OVERDUE", "alert" if late > 7 else "warning", s,
            f"PO {', '.join(s.po_numbers)} overdue {late} day(s)",
            f"Requested ship date {rsd.isoformat()}; no verified cargo-ready or departure.",
            ["Ask the supplier for a firm ready date"], stage=JourneyStage.PRODUCTION_READINESS,
            supplier_id=s.supplier_id, customer_ids=_open_customers(s),
            draft=_draft("supplier", f"PO {', '.join(s.po_numbers)} — ship date",
                         f"PO {', '.join(s.po_numbers)} was due to ship on {rsd.isoformat()}. "
                         "Please confirm the new ready date.\n"))]

    def _unverified_reports(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        out = []
        for e in s.pending_evidence():
            label = e.code.replace("_", " ") if e.code else e.kind
            out.append(_alert(
                "UNVERIFIED_REPORT", "info", s, f"{_ref(s)}: unconfirmed report — {label}",
                f"Reported via {e.source.value} ({e.source_ref or 'no ref'}); not yet verified.",
                ["Confirm or reject this report in the evidence queue"], key=e.evidence_id))
        return out

    def _sequence_exceptions(self, s: Shipment, today: date) -> list[LogisticsAlert]:
        return [_alert(e.code, e.severity, s, f"{_ref(s)}: {e.code.replace('_', ' ').lower()}", e.message,
                       ["Investigate and correct the event record"], key=e.exception_id,
                       stage=e.stage)
                for e in s.open_exceptions()
                if e.code in ("SEQUENCE_ANOMALY", "EVENT_AFTER_CANCEL", "IDENTIFIER_CONFLICT",
                              "DOCUMENT_VALIDATION_FAILED")]

    def _appointment_conflicts(self, shipments: list[Shipment]) -> list[LogisticsAlert]:
        out = []
        by_dock: dict[tuple, list[tuple[datetime, datetime, Shipment]]] = defaultdict(list)
        for s in shipments:
            ap = s.appointment
            st, en = _parse_dt(ap.get("start")), _parse_dt(ap.get("end"))
            if not st or not en:
                continue
            by_dock[(ap.get("warehouse") or s.warehouse, ap.get("dock", ""))].append((st, en, s))
            if s.is_import and not s.has("customs_released"):
                out.append(_alert(
                    "APPOINTMENT_BEFORE_RELEASE", "warning", s,
                    f"{_ref(s)}: warehouse appointment set before customs release",
                    f"Appointment {ap.get('start')} but no verified customs release.",
                    ["Confirm release before dispatching the truck"],
                    stage=JourneyStage.WAREHOUSE_SCHEDULING))
        for (wh, dock), slots in by_dock.items():
            slots.sort(key=lambda x: x[0])
            for i, (st, en, s) in enumerate(slots):
                overlapping = [o for o in slots[:i] if o[1] > st]
                if len(overlapping) >= self.cfg.dock_capacity:
                    other = overlapping[-1][2]
                    out.append(_alert(
                        "APPOINTMENT_CONFLICT", "alert", s,
                        f"Dock conflict at {wh or 'warehouse'} {dock}",
                        f"{_ref(s)} ({st:%Y-%m-%d %H:%M}) overlaps {_ref(other)}.",
                        ["Move one appointment or assign another dock"],
                        stage=JourneyStage.WAREHOUSE_SCHEDULING, key=other.shipment_id))
        return out

    def _supplier_performance(self, shipments: list[Shipment]) -> list[LogisticsAlert]:
        stats: dict[str, dict] = defaultdict(lambda: {"n": 0, "on_time": 0, "received": 0, "damaged": 0,
                                                      "short": 0, "name": ""})
        for s in shipments:
            if not s.supplier_id:
                continue
            st = stats[s.supplier_id]
            st["name"] = s.supplier_name or s.supplier_id
            rsd = _parse_date(s.requested_ship_date)
            actual = _parse_date(s.milestone_date("cargo_ready") or s.milestone_date("vessel_departed"))
            if rsd and actual:
                st["n"] += 1
                st["on_time"] += 1 if (actual - rsd).days <= self.cfg.supplier_on_time_grace_days else 0
            if s.receipt:
                st["received"] += int(s.receipt.get("expected_cases", 0) or 0)
                if s.receipt.get("liability") == "supplier":
                    st["damaged"] += int(s.receipt.get("damaged_cases", 0) or 0)
                    st["short"] += int(s.receipt.get("short_cases", 0) or 0)
        out = []
        for sid, st in stats.items():
            issues = []
            if st["n"] >= 2:
                pct = st["on_time"] / st["n"] * 100
                if pct < self.cfg.supplier_min_on_time_pct:
                    issues.append(f"on-time ship {pct:.0f}% over {st['n']} POs")
            if st["received"]:
                dpct = (st["damaged"] + st["short"]) / st["received"] * 100
                if dpct > self.cfg.supplier_max_damage_pct:
                    issues.append(f"supplier-attributed damage/shortage {dpct:.1f}% of cases")
            if issues:
                out.append(LogisticsAlert(
                    alert_id=_aid("SUPPLIER_PERFORMANCE", sid, ""), code="SUPPLIER_PERFORMANCE",
                    severity="warning", title=f"Supplier performance: {st['name']}",
                    message="; ".join(issues) + ".",
                    recommended_actions=["Review in the next supplier business review",
                                         "Tighten confirmation/quality terms on the next PO"],
                    supplier_id=sid, contains_commercial=True))
        return out


# ─── helpers ──────────────────────────────────────────────────────────────────


def _ref(s: Shipment) -> str:
    if s.container_numbers:
        return f"container {s.container_numbers[0]}"
    if s.bol_number:
        return f"B/L {s.bol_number}"
    if s.po_numbers:
        return f"PO {s.po_numbers[0]}"
    return f"shipment {s.shipment_id}"


def _open_customers(s: Shipment) -> list[str]:
    return sorted({a.customer_id for a in s.allocations if a.status not in ("delivered", "cancelled")})


def _stage_of(code: str) -> Optional[JourneyStage]:
    from .journey import MILESTONES
    spec = MILESTONES.get(code)
    return spec.stage if spec else None


def _aid(code: str, shipment_id: str, key: str) -> str:
    return "alr-" + hashlib.sha256(f"{code}|{shipment_id}|{key}".encode()).hexdigest()[:14]


def _alert(code, severity, s: Shipment, title, message, actions, *, stage=None, key="",
           exposure=0.0, customer_ids=None, supplier_id="", draft=None, commercial=False) -> LogisticsAlert:
    return LogisticsAlert(
        alert_id=_aid(code, s.shipment_id, key), code=code, severity=severity, title=title,
        message=message, shipment_id=s.shipment_id, stage=stage.value if stage else "",
        recommended_actions=actions, draft=draft, financial_exposure_usd=round(float(exposure), 2),
        customer_ids=customer_ids or [], supplier_id=supplier_id or s.supplier_id,
        contains_commercial=commercial)


def _draft(to_role: str, subject: str, body: str, to_id: str = "") -> dict:
    return {"type": "email_draft", "to_role": to_role, "to_id": to_id, "subject": subject,
            "body": body + "\n— Draft prepared by Lola for review. Not sent.",
            "status": "draft", "send": False, "requires_approval": True}


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        d = _parse_date(v)
        if not d:
            return None
        dt = datetime.combine(d, datetime.min.time())
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
