"""Guard against client-faked logistics milestones, receipts and resolutions."""
import pytest

from app.intelligence.wholesale_distribution.logistics.journey import (
    ConfirmerRequiredError, LedgerError, LogisticsEvent, ShipmentLedger, SourceType,
)


def ledger_with_shipment():
    ledger = ShipmentLedger("company-A")
    ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="shipment_created",
        source=SourceType.ERP, payload={"po_numbers": ["PO-1"]},
    ))
    return ledger


def test_unverified_report_cannot_confirm_another_event():
    ledger = ledger_with_shipment()
    reported = ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="milestone",
        code="customs_released", source=SourceType.EMAIL_PARSED,
        source_ref="gmail-1",
    ))
    assert reported.evidence_id
    with pytest.raises(ConfirmerRequiredError, match="named person"):
        ledger.ingest(LogisticsEvent(
            company_id="company-A", shipment_id="SHP-1",
            kind="evidence_confirmed", source=SourceType.MANUAL_NOTE,
            source_ref=reported.evidence_id,
        ))
    before = ledger.get("SHP-1")
    assert "customs_released" not in before.milestones
    assert before.pending_evidence()[0].status == "unverified"


def test_unverified_customs_exception_cannot_be_removed():
    ledger = ledger_with_shipment()
    created = ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="exception",
        source=SourceType.ERP,
        payload={"code": "CUSTOMS_HOLD", "message": "Check broker release"},
    ))
    exception_id = created.exceptions[0].exception_id
    with pytest.raises(ConfirmerRequiredError, match="unverified"):
        ledger.ingest(LogisticsEvent(
            company_id="company-A", shipment_id="SHP-1", kind="exception_resolved",
            source=SourceType.EMAIL_PARSED, payload={"exception_id": exception_id},
        ))
    assert any(not x.resolved for x in ledger.get("SHP-1").exceptions)


def test_unverified_receipt_is_evidence_not_warehouse_receipt():
    ledger = ledger_with_shipment()
    reported = ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="receipt_reconciled",
        source=SourceType.AI_INFERRED, source_ref="generated-summary",
        payload={"summary": {"good_cases": 1200}},
    ))
    assert reported.verified is False
    assert reported.evidence_id
    assert ledger.get("SHP-1").receipt == {}
    with pytest.raises(LedgerError, match="controlled receiving workflow"):
        ledger.confirm_evidence(reported.evidence_id, confirmed_by="manager-1")
    assert ledger.get("SHP-1").receipt == {}
    assert ledger.get("SHP-1").pending_evidence()[0].status == "unverified"


def test_trusted_internal_receipt_event_still_supported():
    ledger = ledger_with_shipment()
    result = ledger.ingest(LogisticsEvent(
        company_id="company-A", shipment_id="SHP-1", kind="receipt_reconciled",
        source=SourceType.USER_CONFIRMED, confirmed_by="warehouse-manager-1",
        payload={"summary": {"good_cases": 1180, "damaged_cases": 20}},
    ))
    assert result.applied
    assert ledger.get("SHP-1").receipt["good_cases"] == 1180
    assert ledger.get("SHP-1").receipt["damaged_cases"] == 20
