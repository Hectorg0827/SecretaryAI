"""Chief-of-Staff passive briefing: synthetic observations, no API calls."""
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from app.intelligence.lola import briefing


@dataclass
class FakeAlert:
    alert_id: str
    code: str = "CUSTOMS_HOLD"
    severity: str = "critical"
    title: str = "Customs hold"
    message: str = "Broker action required"
    shipment_id: str = "shipment-A"
    priority: int = 110
    recommended_actions: tuple = ("Contact broker",)
    draft: dict | None = None
    financial_exposure_usd: float | None = 123.0

    def to_dict(self):
        return dict(self.__dict__)


class FakeViewer:
    def __init__(self, user): self.user = user
    @classmethod
    def from_user(cls, user): return cls(user)
    def require_access(self):
        if self.user.get("role") not in ("owner", "manager", "back_office"):
            raise PermissionError("restricted")
    def visible(self, shipments): return shipments
    def filter_alerts(self, alerts, shipments):
        self.require_access()
        return [
            {**a.to_dict(), "financial_exposure_usd": None}
            if self.user["role"] == "back_office" else a.to_dict()
            for a in alerts
        ]


class FakeLedger:
    def __init__(self, company_id="tenant-A", count=1):
        self.company_id = company_id
        self._shipments = [
            SimpleNamespace(shipment_id=f"shipment-{i}", company_id=company_id)
            for i in range(count)
        ]
        self._events = [
            SimpleNamespace(event_id=f"event-{i}", company_id=company_id)
            for i in range(count)
        ]
    def shipments(self): return self._shipments
    def events(self): return self._events


@pytest.fixture(autouse=True)
def adapters(monkeypatch):
    monkeypatch.setattr(briefing, "LogisticsViewer", FakeViewer)
    monkeypatch.setattr(
        briefing, "LogisticsMonitor",
        lambda ledger: SimpleNamespace(
            scan=lambda as_of: [FakeAlert("alert-A")] if ledger.shipments() else []
        ),
    )


def report(role="owner", **kwargs):
    return briefing.build_chief_of_staff_briefing(
        user={"company_id": "tenant-A", "role": role},
        ledger=FakeLedger(**kwargs), as_of=date(2026, 11, 14),
    )


def test_passive_source_labeled_cards():
    body = report()
    assert body["state"] == "needs_attention"
    assert body["visible_alert_count"] == 1
    assert body["cards"][0]["evidence_status"] == "derived_from_logistics_ledger"
    assert body["cards"][0]["action_status"] == "review_only_not_staged"
    assert body["external_writes_executed"] is False
    assert body["actions_staged"] is False
    assert body["proactive_voice_enabled"] is False
    assert body["reconciled_with_quickbooks"] is False


def test_redacted_financial_values_stay_redacted():
    body = report(role="back_office")
    assert body["cards"][0]["estimated_financial_exposure_usd"] is None
    assert body["cards"][0]["financial_exposure_kind"] == "restricted"


def test_cross_tenant_ledger_fails_closed():
    with pytest.raises(briefing.BriefingUnavailable):
        report(company_id="tenant-B")


def test_cross_tenant_event_fails_closed():
    ledger = FakeLedger()
    ledger._events[0].company_id = "tenant-B"
    with pytest.raises(briefing.BriefingUnavailable):
        briefing.build_chief_of_staff_briefing(
            user={"company_id": "tenant-A", "role": "owner"},
            ledger=ledger, as_of=date(2026, 11, 14),
        )


def test_empty_ledger_is_not_nominal_business_state():
    body = report(count=0)
    assert body["state"] == "no_visible_shipments"
    assert body["visible_alert_count"] == 0
    assert "not proof" in body["coverage_note"]


def test_permission_denied():
    with pytest.raises(PermissionError):
        report(role="viewer")


def test_invalid_bounds_fail():
    with pytest.raises(ValueError):
        briefing.build_chief_of_staff_briefing(
            user={"company_id": "tenant-A"},
            ledger=FakeLedger(), as_of=date(2026, 11, 14), limit=0,
        )
    with pytest.raises(ValueError):
        briefing.build_chief_of_staff_briefing(
            user={"company_id": "tenant-A"},
            ledger=FakeLedger(), as_of="2026-11-14",
        )


def test_snapshot_trace_stable_for_identical_record_set():
    assert report()["source_event_fingerprint"] == report()["source_event_fingerprint"]


def test_unverified_reports_are_labeled(monkeypatch):
    monkeypatch.setattr(
        briefing, "LogisticsMonitor",
        lambda ledger: SimpleNamespace(
            scan=lambda as_of: [FakeAlert("pending", code="UNVERIFIED_REPORT")]
        ),
    )
    assert report()["cards"][0]["evidence_status"] == "unverified_report_requires_confirmation"


def test_truncation_retains_full_count(monkeypatch):
    monkeypatch.setattr(
        briefing, "LogisticsMonitor",
        lambda ledger: SimpleNamespace(
            scan=lambda as_of: [FakeAlert(f"alert-{i}") for i in range(7)]
        ),
    )
    body = briefing.build_chief_of_staff_briefing(
        user={"company_id": "tenant-A", "role": "owner"},
        ledger=FakeLedger(), as_of=date(2026, 11, 14), limit=2,
    )
    assert body["visible_alert_count"] == 7
    assert len(body["cards"]) == 2
    assert body["cards_truncated"] is True
