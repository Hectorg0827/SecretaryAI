"""
Tests for Stage 3 — PO Transmission & Vendor Response Tracking.
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.intelligence.wholesale_distribution.logistics.vendor_tracker import (
    VendorResponseParser,
    VendorTracker,
)
from app.intelligence.wholesale_distribution.logistics.pipeline import (
    PurchaseOrder,
    SupplyChainType,
)


def _make_po(po_number="PO-2024-001", supplier_id="sup-001"):
    return PurchaseOrder(
        po_number=po_number,
        supplier_id=supplier_id,
        supplier_name="Test Supplier S.A.",
        supplier_email="orders@testsupplier.com",
        ship_to_address="123 Warehouse Blvd, Los Angeles, CA",
        payment_terms="Net 30",
        requested_ship_date="2025-04-15",
    )


def _make_tracker():
    return VendorTracker(
        company_id="test-co",
        follow_up_hours_overseas=72,
        follow_up_hours_domestic=24,
    )


# ── VendorResponseParser ───────────────────────────────────────────────────────


def test_parses_english_confirmation():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="Re: PO-2024-001 Confirmation",
        body="We confirm receipt of your purchase order. We will ship on April 15, 2026.",
        po=po,
    )
    assert result.order_confirmed is True


def test_parses_spanish_confirmation():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="Confirmación de pedido PO-2024-001",
        body="Confirmamos la recepción de su orden. Enviaremos el 20 de abril de 2026.",
        po=po,
    )
    assert result.order_confirmed is True


def test_parses_rejection():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="PO-2024-001",
        body="Unfortunately we are unable to fulfill this order due to stock shortages.",
        po=po,
    )
    assert result.rejection is True
    assert result.order_confirmed is False


def test_extracts_ship_date_natural_language():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="PO Confirmed",
        body="We will ship your order on April 20, 2026.",
        po=po,
    )
    assert result.ship_date is not None
    assert "2026" in result.ship_date


def test_detects_issues_in_email():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="PO Issue",
        body="There is a delay in shipping. Potential delay of 2 weeks.",
        po=po,
    )
    assert len(result.issues) > 0


def test_no_issues_in_clean_confirmation():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="Order Confirmed",
        body="We confirm order PO-2024-001. Ship date April 15, 2026.",
        po=po,
    )
    assert result.issues == []


def test_ambiguous_email_requires_review():
    parser = VendorResponseParser()
    po = _make_po()
    result = parser.parse(
        subject="Thank you",
        body="Thank you for your message. We will get back to you.",
        po=po,
    )
    # No confirmation or rejection pattern → requires review
    assert result.requires_human_review is True or result.order_confirmed is None


# ── VendorTracker — Overdue Detection ─────────────────────────────────────────


def test_overdue_pos_detected_for_overseas():
    tracker = _make_tracker()
    po = _make_po()
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()

    overdue = tracker.check_overdue_pos([{
        "po": po,
        "sent_at": sent_at,
        "supply_chain_type": "overseas",
        "follow_up_count": 0,
    }])
    assert len(overdue) == 1
    assert overdue[0]["po_number"] == "PO-2024-001"
    assert overdue[0]["action"] == "draft_follow_up"


def test_not_overdue_within_sla():
    tracker = _make_tracker()
    po = _make_po()
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

    overdue = tracker.check_overdue_pos([{
        "po": po,
        "sent_at": sent_at,
        "supply_chain_type": "overseas",  # SLA = 72h
        "follow_up_count": 0,
    }])
    assert len(overdue) == 0


def test_domestic_sla_is_shorter():
    tracker = _make_tracker()
    po = _make_po()
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()

    overdue = tracker.check_overdue_pos([{
        "po": po,
        "sent_at": sent_at,
        "supply_chain_type": "domestic",  # SLA = 24h → this is overdue
        "follow_up_count": 0,
    }])
    assert len(overdue) == 1


def test_overdue_escalates_after_second_follow_up():
    tracker = _make_tracker()
    po = _make_po()
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()

    overdue = tracker.check_overdue_pos([{
        "po": po,
        "sent_at": sent_at,
        "supply_chain_type": "overseas",
        "follow_up_count": 2,  # already 2 follow-ups → manual intervention
    }])
    assert len(overdue) == 1
    assert overdue[0]["action"] == "manual_intervention_required"


def test_second_follow_up_is_urgent():
    tracker = _make_tracker()
    po = _make_po()
    sent_at = (datetime.now(timezone.utc) - timedelta(hours=150)).isoformat()

    overdue = tracker.check_overdue_pos([{
        "po": po,
        "sent_at": sent_at,
        "supply_chain_type": "overseas",
        "follow_up_count": 1,  # one follow-up done → send final
    }])
    assert len(overdue) == 1
    assert overdue[0]["action"] == "send_final_follow_up"
    assert overdue[0]["severity"] == "red"
