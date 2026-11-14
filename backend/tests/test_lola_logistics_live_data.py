"""Lola logistics bridge: role scope, provenance and outage fail-closed checks.

All records here are synthetic. No QuickBooks, carrier API or external service is queried.
"""
import json

import pytest
from fastapi import HTTPException

from app.api.chat import _verified_logistics_chat_context
from app.intelligence.wholesale_distribution.logistics.journey import (
    LogisticsEvent, SourceType,
)
from app.intelligence.wholesale_distribution.logistics import store as source_store


def _events():
    return [
        LogisticsEvent(
            company_id="company-A", shipment_id="SHP-TEST-1", kind="shipment_created",
            source=SourceType.USER_CONFIRMED, confirmed_by="owner-1",
            payload={
                "container_numbers": ["MSCU1234566"], "origin": "Santo Domingo",
                "destination": "New York", "po_numbers": ["PO-TEST-1"],
            },
        ),
        LogisticsEvent(
            company_id="company-A", shipment_id="SHP-TEST-1", kind="eta",
            source=SourceType.EMAIL_PARSED, source_ref="email-test-1",
            payload={"eta": "2026-11-22", "location": "destination_port"},
        ),
    ]


def _seed_store(monkeypatch, fail=False):
    class FakeStore:
        def __init__(self, db):
            self.db = db

        def load(self, company_id):
            if fail:
                raise RuntimeError("Private transport error must not leak")
            return [event for event in _events() if event.company_id == company_id]

    monkeypatch.setattr(source_store, "SupabaseEventStore", FakeStore)


def test_lola_logistics_uses_company_events_without_promoting_email_eta(monkeypatch):
    _seed_store(monkeypatch)
    response = json.loads(_verified_logistics_chat_context(
        {"company_id": "company-A", "sub": "owner-1", "role": "owner"},
        object(), "where is container MSCU1234566?",
    ))
    assert response["source"] == "tenant_scoped_logistics_events"
    assert response["recorded_visible_shipments"] == 1
    answer = response["answer"]
    assert answer["intent"] == "where_is"
    assert "no verified ETA" in answer["answer"]
    assert "2026-11-22" in answer["answer"]
    assert "unverified" in answer["answer"]
    assert "PO-TEST-1" in json.dumps(answer["links"])


def test_lola_logistics_never_sees_other_company_shipment(monkeypatch):
    _seed_store(monkeypatch)
    response = json.loads(_verified_logistics_chat_context(
        {"company_id": "company-B", "sub": "owner-2", "role": "owner"},
        object(), "where is container MSCU1234566?",
    ))
    assert response["recorded_visible_shipments"] == 0
    assert "couldn't find" in response["answer"]["answer"].lower()
    assert "PO-TEST-1" not in json.dumps(response)


def test_lola_logistics_rejects_viewer_before_loading_store(monkeypatch):
    _seed_store(monkeypatch)
    with pytest.raises(HTTPException) as error:
        _verified_logistics_chat_context(
            {"company_id": "company-A", "sub": "viewer-1", "role": "viewer"},
            object(), "where is container MSCU1234566?",
        )
    assert error.value.status_code == 403


def test_lola_logistics_unavailable_store_is_not_empty_result(monkeypatch):
    _seed_store(monkeypatch, fail=True)
    with pytest.raises(HTTPException) as error:
        _verified_logistics_chat_context(
            {"company_id": "company-A", "sub": "owner-1", "role": "owner"},
            object(), "what shipments are delayed?",
        )
    assert error.value.status_code == 503
    assert "No shipment status was inferred" in error.value.detail
    assert "Private transport" not in error.value.detail
