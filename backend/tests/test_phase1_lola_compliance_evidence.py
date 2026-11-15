"""Lola compliance responses require tenant evidence, not generic AI statements."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from app.api import compliance as compliance_api
from app.api.chat import _verified_compliance_context, _guard_compliance_answer
from app.ai.data_summarizer import build_query_context


USER = {"company_id": "company-a", "sub": "owner-a", "role": "owner"}
COUNTS = {
    "products_count": 2,
    "licenses_count": 1,
    "federal_permits_count": 1,
    "distributors_count": 0,
    "brand_registrations_count": 0,
    "colas_count": 0,
}


def _mock_sources(monkeypatch, *, setup: bool, missing=None):
    monkeypatch.setattr(
        compliance_api, "get_setup_status",
        AsyncMock(return_value={"is_setup": setup, "missing": missing or [], **COUNTS}),
    )
    monkeypatch.setattr(
        compliance_api, "get_compliance_status",
        AsyncMock(return_value={"overall": "ok", "active_states": ["NY"]}),
    )
    monkeypatch.setattr(
        compliance_api, "get_compliance_alerts",
        AsyncMock(return_value={"count": 1, "alerts": [
            {"priority": "critical", "item_name": "Synthetic permit", "state_code": "NY"}
        ]}),
    )


@pytest.mark.asyncio
async def test_lola_marks_missing_compliance_setup_as_unverified(monkeypatch):
    _mock_sources(monkeypatch, setup=False, missing=["brand_registrations", "licenses"])
    data = await _verified_compliance_context(USER, object())
    context = data["compliance"]
    assert context["overall_status"] == "unverified"
    assert context["counts"]["colas_count"] == 0
    summary = build_query_context("compliance_check", data)
    assert "INCOMPLETE" in summary
    assert "brand_registrations" in summary
    assert "not independently verified with regulators" in summary
    assert "source unavailable" not in summary.lower()


@pytest.mark.asyncio
async def test_lola_compliance_does_not_give_sales_rep_company_access(monkeypatch):
    _mock_sources(monkeypatch, setup=True)
    with pytest.raises(HTTPException) as error:
        await _verified_compliance_context(
            {"company_id": "company-a", "role": "sales_rep"}, object()
        )
    assert error.value.status_code == 403
    compliance_api.get_setup_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_lola_compliance_propagates_registry_outage(monkeypatch):
    _mock_sources(monkeypatch, setup=True)
    compliance_api.get_setup_status.side_effect = HTTPException(
        status_code=503, detail="Synthetic compliance registry failed"
    )
    with pytest.raises(HTTPException) as error:
        await _verified_compliance_context(USER, object())
    assert error.value.status_code == 503
    compliance_api.get_compliance_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_lola_compliance_refuses_incomplete_source_counts(monkeypatch):
    _mock_sources(monkeypatch, setup=True)
    response = {"is_setup": True, "missing": [], **COUNTS}
    del response["colas_count"]
    compliance_api.get_setup_status.return_value = response
    with pytest.raises(HTTPException) as error:
        await _verified_compliance_context(USER, object())
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_complete_registry_is_not_misrepresented_as_regulator_certification(monkeypatch):
    _mock_sources(monkeypatch, setup=True)
    data = await _verified_compliance_context(USER, object())
    assert data["compliance"]["overall_status"] == "ok"
    text = build_query_context("compliance_check", data)
    assert "not independently verified with regulators" in text
    assert "not proof of current license" in text
    assert "Synthetic permit" in text


def test_zero_colas_in_internal_registry_are_not_proof_of_legal_prohibition():
    data = {"compliance": {
        "source": "company-scoped Lion BI compliance registry",
        "is_setup": False, "missing": ["colas"], "overall_status": "unverified",
        "counts": {**COUNTS, "colas_count": 0}, "alerts_count": 0,
    }}
    summary = build_query_context("compliance_check", data)
    assert "Zero COLAs" in summary
    assert "does NOT prove" in summary
    assert "illegal or blocked" in summary
    assert "legal status is unverified" in summary


@pytest.mark.parametrize("unsupported", [
    "There are no COLAs on file, so you cannot legally import.",
    "Importation is blocked until you register every COLA.",
    "The importation and sale of these products would be blocked.",
    "The business is fully compliant because no alerts were found.",
    "You are prohibited from selling the product.",
])
def test_lola_regulator_claim_is_replaced_before_streaming(unsupported):
    evidence = {"counts": {"colas_count": 0}, "is_setup": False,
                "source_quality": "registry_observed_not_regulator_verified"}
    guarded = _guard_compliance_answer(unsupported, evidence)
    assert guarded != unsupported
    assert "0 COLA records are recorded in Lion BI" in guarded
    assert "UNVERIFIED" in guarded
    assert "neither a legal ban nor regulatory clearance" in guarded


def test_accurate_registry_uncertainty_is_preserved():
    evidence = {"counts": {"colas_count": 0}, "is_setup": False}
    good = (
        "The internal registry contains zero COLAs on file. That does not "
        "establish whether external approvals exist, so clearance is unverified."
    )
    assert _guard_compliance_answer(good, evidence) == good
