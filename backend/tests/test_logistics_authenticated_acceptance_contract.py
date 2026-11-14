"""Synthetic tests for the GET-only logistics acceptance probe."""
import pytest

from scripts.logistics_authenticated_acceptance import (
    AcceptanceBlocked, check_logistics_sources,
)

class ReadOnlyFakeClient:
    def __init__(self, fixtures):
        self.fixtures = fixtures
        self.get_paths = []

    def get(self, path):
        import httpx
        self.get_paths.append(path)
        if path not in self.fixtures:
            return httpx.Response(503, json={"detail": "provider unavailable"})
        return httpx.Response(200, json=self.fixtures[path])


def data(shipped=True):
    rows = ([{"shipment_id": "SHIP-1", "current_stage_label": "Waiting"}]
            if shipped else [])
    data = {
        "/api/logistics/journey/shipments": {"shipments": rows, "count": len(rows)},
        "/api/logistics/journey/purchasing-coverage": {
            "orders": [{
                "po_number": "PO-1", "linked_shipment_ids": ["SHIP-1"] if shipped else [],
                "shipping_status_verified": False,
                "container_receipt_verified": False,
                "warehouse_receipt_attributed_to_shipment": False,
            }], "purchase_order_count": 1, "not_linked_count": 0 if shipped else 1,
            "shipment_status_verified_by_po_link": False,
            "inventory_posted_by_this_operation": False,
        },
        "/api/logistics/monitor": {"alerts": [], "count": 0},
    }
    if shipped:
        data.update({
            "/api/logistics/journey/shipments/SHIP-1": {
                "shipment_id": "SHIP-1", "po_numbers": ["PO-1"],
                "stages": [{"stage": str(i)} for i in range(14)],
            },
            "/api/logistics/journey/shipments/SHIP-1/purchasing-crosscheck": {
                "shipment_id": "SHIP-1",
                "po_results": [{"po_number": "PO-1", "status": "approved"}],
                "posted_receipts_prove_shipment_received": False,
                "shipment_inventory_reconciled": False, "accounting_reconciled": False,
            },
        })
    return data


def test_realistic_recorded_journey_cannot_be_called_external_acceptance():
    client = ReadOnlyFakeClient(data())
    result = check_logistics_sources(client)
    assert result["recorded_shipment_count"] == 1
    assert result["read_only_source_parity"] == "CHECKED"
    assert result["carrier_customs_warehouse_evidence_independently_verified"] is False
    assert result["quickbooks_accounting_reconciled"] is False
    assert result["acceptance"] == "SOURCE_PARITY_OBSERVED_EXTERNAL_EVIDENCE_PENDING"
    assert len(client.get_paths) == 5
    assert all("/api/logistics/" in p for p in client.get_paths)


def test_zero_shipment_events_is_explicitly_blocked_not_live_clearance():
    client = ReadOnlyFakeClient(data(shipped=False))
    result = check_logistics_sources(client)
    assert result["recorded_shipment_count"] == 0
    assert result["saved_po_count"] == 1
    assert result["acceptance"] == "BLOCKED_NO_RECORDED_SHIPMENTS"
    assert len(client.get_paths) == 3


def test_crosscheck_false_certification_causes_blocker():
    fixtures = data()
    fixtures["/api/logistics/journey/shipments/SHIP-1/purchasing-crosscheck"][
        "shipment_inventory_reconciled"
    ] = True
    with pytest.raises(AcceptanceBlocked, match="falsely certifies"):
        check_logistics_sources(ReadOnlyFakeClient(fixtures))


def test_crosscheck_mismatched_po_is_a_blocker():
    fixtures = data()
    fixtures["/api/logistics/journey/shipments/SHIP-1/purchasing-crosscheck"]["po_results"] = [
        {"po_number": "PO-OTHER", "status": "received"}
    ]
    with pytest.raises(AcceptanceBlocked, match="purchase references disagree"):
        check_logistics_sources(ReadOnlyFakeClient(fixtures))


def test_unavailable_backend_never_returns_pass():
    fixtures = data()
    del fixtures["/api/logistics/journey/purchasing-coverage"]
    with pytest.raises(AcceptanceBlocked, match="503"):
        check_logistics_sources(ReadOnlyFakeClient(fixtures))


def test_malformed_or_broken_14_stage_projection_blocks():
    fixtures = data()
    fixtures["/api/logistics/journey/shipments/SHIP-1"]["stages"] = []
    with pytest.raises(AcceptanceBlocked, match="Fourteen-stage"):
        check_logistics_sources(ReadOnlyFakeClient(fixtures))


def test_unobserved_external_shipment_reference_is_not_silently_hidden():
    fixtures = data()
    fixtures["/api/logistics/journey/purchasing-coverage"]["orders"][0]["linked_shipment_ids"] = [
        "OTHER-COMPANY-SHIP"
    ]
    with pytest.raises(AcceptanceBlocked, match="not in the complete"):
        check_logistics_sources(ReadOnlyFakeClient(fixtures))
