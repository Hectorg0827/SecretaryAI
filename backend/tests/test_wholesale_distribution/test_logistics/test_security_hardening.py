"""
Security regressions for logistics input handling:

  * document regexes stay linear on hostile OCR text (CodeQL py/polynomial-redos)
  * values written to logs cannot forge extra log lines (CodeQL py/log-injection)
  * 422 responses keep list positions and the request ID, and never echo input
"""
import logging
import re
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, field_validator

import app.api.logistics as logistics_api
from app.intelligence.wholesale_distribution.logistics import documents
from app.intelligence.wholesale_distribution.logistics.documents import (
    MAX_OCR_TEXT_LENGTH,
    DocumentIntelligence,
    broker_status,
)
from app.middleware.request_id import RequestIDMiddleware
from app.utils.error_handler import register_error_handlers

# ─── Document extraction: correct values ─────────────────────────────────────

SAMPLE = """BILL OF LADING
B/L No: MAEU123456789
Booking Number: BK-778899
PO #: PO-4471
Container: MSCU 123456 6
Seal No: SL-99812
Entry Number: 123-4567890-1
SKU: COLA-12OZ
2,400 cases
"""


def test_extracts_each_identifier_from_a_normal_document():
    f = DocumentIntelligence().extract(SAMPLE).fields
    assert f["bol_number"] == "MAEU123456789"
    assert f["booking_ref"] == "BK-778899"
    assert "PO-4471" in f["po_numbers"]
    assert f["seal_numbers"] == ["SL-99812"]
    assert f["entry_number"] == "123-4567890-1"
    assert "COLA-12OZ" in f["skus"]
    assert f["quantities_cases"] == [2400]
    # MSCU1234566 is a valid ISO 6346 number (check digit 6).
    assert f["container_numbers"] == ["MSCU1234566"]


def test_negated_release_is_not_read_as_released():
    assert broker_status("Cargo has  not been released yet") == "not_released"
    assert broker_status("Release is   pending") == "not_released"
    assert broker_status("Cargo release issued") == "released"


# ─── Document extraction: hostile input stays fast ───────────────────────────

_ALL_PATTERNS = {k: v for k, v in vars(documents).items() if isinstance(v, re.Pattern)}
_ALL_PATTERNS.update({f"date{i}": p for i, (p, _) in enumerate(documents._DATE_PATTERNS)})
_SEEDS = [" ", "1", "1,", "-", ":", "#", ".", "\t", "po ", "po no ", "seal ", "1 cases ",
          "short ", "total ", "not ", "release ", "signed by a", "eta ", "AAAU", "item A-", "0,000"]


@pytest.mark.parametrize("seed", _SEEDS)
def test_every_pattern_is_fast_on_25k_repeating_input(seed):
    text = seed * (25_000 // len(seed)) + "!"
    for name, rx in _ALL_PATTERNS.items():
        start = time.perf_counter()
        list(rx.finditer(text))
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"{name} took {elapsed * 1000:.0f} ms on {seed!r}"


def test_oversized_text_is_cut_to_the_limit_before_scanning():
    tail = "\nB/L No: TAILVALUE99999"
    doc = DocumentIntelligence().extract("x" * MAX_OCR_TEXT_LENGTH + tail)
    assert doc.fields["bol_number"] == ""          # beyond the limit: not scanned


def test_limit_matches_the_api_upload_limit():
    # A lower limit would silently drop fields from documents the API accepts.
    field = logistics_api.DocumentIngestRequest.model_fields["text"]
    max_len = next(m.max_length for m in field.metadata if hasattr(m, "max_length"))
    assert MAX_OCR_TEXT_LENGTH >= max_len


# ─── Log sanitising ──────────────────────────────────────────────────────────

_FORGED = "ship-1\r\nCRITICAL fake admin login\x00"


def test_sanitize_log_value_strips_control_characters_and_caps_length():
    out = logistics_api._sanitize_log_value(_FORGED)
    assert "\r" not in out and "\n" not in out and "\x00" not in out
    assert logistics_api._sanitize_log_value(None) == ""
    assert len(logistics_api._sanitize_log_value("a" * 1000)) == 256


def _assert_clean(caplog):
    assert caplog.records, "expected a log record"
    for record in caplog.records:
        text = record.getMessage()
        assert "\r" not in text and "\n" not in text and "\x00" not in text


def test_load_ledger_failure_log_has_no_control_characters(caplog):
    user = {"company_id": "co-1\r\nforged"}
    with patch.object(logistics_api.ShipmentLedger, "load", side_effect=RuntimeError(_FORGED)), \
            caplog.at_level(logging.ERROR, logger=logistics_api.log.name):
        with pytest.raises(logistics_api.HTTPException) as err:
            logistics_api._load_ledger(user, db=object())
    assert err.value.status_code == 503
    _assert_clean(caplog)


def test_record_email_evidence_failure_log_has_no_control_characters(caplog):
    user = {"company_id": "co-1"}
    with patch.object(logistics_api.ShipmentLedger, "load", side_effect=RuntimeError(_FORGED)), \
            caplog.at_level(logging.WARNING, logger=logistics_api.log.name):
        result = logistics_api._record_email_evidence(user, object(), _FORGED, "eta_updated")
    assert result is None                              # still best-effort, unchanged
    _assert_clean(caplog)


# ─── 422 responses ───────────────────────────────────────────────────────────


class _Line(BaseModel):
    qty: int


class _Order(BaseModel):
    lines: list[_Line]
    metadata: dict[str, int] = {}

    @field_validator("metadata")
    @classmethod
    def no_secrets(cls, value):
        if "api_key" in value:
            raise ValueError(f"metadata contained a secret: {value}")
        return value


@pytest.fixture()
def client():
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(RequestIDMiddleware)

    @app.post("/order")
    async def order(body: _Order):
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False)


def test_422_keeps_list_positions(client):
    resp = client.post("/order", json={"lines": [{"qty": 1}, {"qty": "many"}]})
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "lines", 1, "qty"]


def test_422_hides_dictionary_keys_and_custom_validator_text(client):
    resp = client.post("/order", json={"lines": [], "metadata": {"tok-SECRET-KEY": "x"}})
    assert resp.status_code == 422
    assert "tok-SECRET-KEY" not in resp.text
    assert resp.json()["detail"][0]["loc"] == ["body", "metadata", "<redacted_key>"]

    resp = client.post("/order", json={"lines": [], "metadata": {"api_key": 12345}})
    assert resp.status_code == 422                    # not 500
    assert "12345" not in resp.text and "secret" not in resp.text
    for item in resp.json()["detail"]:
        assert set(item) == {"type", "loc", "msg"}


def test_422_carries_the_request_id(client):
    resp = client.post("/order", json={"lines": "nope"}, headers={"X-Request-ID": "trace-123"})
    assert resp.status_code == 422
    assert resp.headers["X-Request-ID"] == "trace-123"
