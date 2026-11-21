"""Owner-only QBO AR preview: staging flag, DB role and redacted response.

All fixtures are synthetic; no provider credentials, network or financial writes.
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Response

from app.api.finance_ar_preview import (
    ARPaymentPreviewResponse, get_ar_payment_preview, router,
)


@pytest.fixture(autouse=True)
def _synthetic_staging_support(monkeypatch):
    # Only this route-handler test module skips real Redis/companies DB;
    # the real helpers are verified in test_finance_qbo_only_adapter.py.
    monkeypatch.setattr(
        "app.api.finance_ar_preview.claim_qbo_ar_preview_window",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        "app.api.finance_ar_preview.verify_qbo_ar_preview_connection",
        lambda **kwargs: None,
    )


def settings(environment="staging", enabled=True):
    return SimpleNamespace(
        secretaryai_deployment_env=environment,
        secretaryai_ar_payment_preview_enabled=enabled,
    )


def user(role="owner"):
    return {"company_id": "tenant-fixture", "sub": "owner-fixture", "role": role}


def evidence():
    return {
        "status": "aligned_not_certified",
        "reason": None,
        "invoice_count": 2,
        "linked_invoice_count": 1,
        "variance_invoice_count": 0,
        "partially_paid_invoice_count": 1,
        "unapplied_payment_count": 1,
        "accounting_reconciled": False,
        "financial_write_authorized": False,
    }


def test_preview_registered_only_as_get_and_has_response_schema():
    route = next(x for x in router.routes if x.path == "/ar-payment-preview")
    assert route.methods == {"GET"}
    assert route.response_model is ARPaymentPreviewResponse


@pytest.mark.asyncio
@pytest.mark.parametrize("env,enabled", [
    ("production", True), ("staging", False),
    ("development", True), ("staging", None),
])
async def test_preview_disabled_before_db_role_or_adapter_access(env, enabled):
    response = Response()
    with patch("app.api.finance_ar_preview.get_settings",
               return_value=settings(env, enabled)), \
         patch("app.api.finance_ar_preview._current_role") as verify_role, \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter") as adapter:
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(response, user=user(), db=MagicMock())
    assert failure.value.status_code == 404
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Cache-Control"] == "private, no-store"
    verify_role.assert_not_called()
    adapter.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["manager", "ar_specialist", "ap_specialist", "viewer"])
async def test_preview_refuses_nonowner_after_current_db_role_check(role):
    response = Response()
    with patch("app.api.finance_ar_preview.get_settings",
               return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role",
               return_value=role) as verify_role, \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter") as adapter:
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(response, user=user(role), db=MagicMock())
    assert failure.value.status_code == 403
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    verify_role.assert_called_once()
    adapter.assert_not_called()


@pytest.mark.asyncio
async def test_stale_or_revoked_owner_role_blocks_provider_before_construction():
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role",
               side_effect=HTTPException(status_code=403)), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter") as adapter:
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(
                Response(), user=user(), db=MagicMock(),
            )
    assert failure.value.status_code == 403
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    adapter.assert_not_called()


@pytest.mark.asyncio
async def test_owner_preview_returns_aggregate_only_and_explicit_noncertification():
    response = Response()
    db = MagicMock()
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(return_value=evidence()),
        create_payment=AsyncMock(),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role",
               return_value="owner") as role_check, \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb) as adapter:
        result = await get_ar_payment_preview(response, user=user(), db=db)
    assert response.headers["Cache-Control"] == "private, no-store"
    assert role_check.call_count == 2
    role_check.assert_any_call(db, user())
    adapter.assert_called_once_with(user=user(), db=db)
    qb.get_qbo_ar_payment_match_preview_read_only.assert_awaited_once()
    args = qb.get_qbo_ar_payment_match_preview_read_only.await_args.kwargs
    assert args["company_id"] == "tenant-fixture"
    assert isinstance(args["as_of"], date)
    assert result.source_system == "qbo_sandbox"
    assert result.source_quality == "partial"
    assert result.status == "aligned_not_certified"
    assert result.accounting_reconciled is False
    assert result.financial_write_authorized is False
    assert "tenant-fixture" not in result.model_dump_json()
    assert "owner-fixture" not in result.model_dump_json()
    assert "SENSITIVE" not in result.model_dump_json()
    qb.create_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_source_failure_returns_generic_503_and_never_zero():
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(
            side_effect=RuntimeError("SENSITIVE_PROVIDER_RESPONSE_CANARY")
        ),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role", return_value="owner"), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb):
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(
                Response(), user=user(), db=MagicMock(),
            )
    assert failure.value.status_code == 503
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert "SENSITIVE" not in failure.value.detail
    assert "0" not in failure.value.detail


@pytest.mark.asyncio
async def test_response_schema_rejects_provider_identity_or_money_injection():
    data = dict(evidence())
    data.update(customer_name="PRIVATE", receipt_amount="5000000")
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(return_value=data),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role", return_value="owner"), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb):
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(
                Response(), user=user(), db=MagicMock(),
            )
    assert failure.value.status_code == 503
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert "PRIVATE" not in failure.value.detail

COUNT_FIELDS = (
    "invoice_count",
    "linked_invoice_count",
    "variance_invoice_count",
    "partially_paid_invoice_count",
    "unapplied_payment_count",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", COUNT_FIELDS)
@pytest.mark.parametrize("invalid", [-1, True, "2"])
async def test_invalid_aggregate_count_fails_closed_and_no_financial_write(
    field, invalid,
):
    # Deliberately malformed provider evidence must never be reported as
    # an aligned receivables result, silently coerced, or leaked to the caller.
    source = dict(evidence())
    source[field] = invalid
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(return_value=source),
        create_payment=AsyncMock(),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role", return_value="owner"), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb):
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(Response(), user=user(), db=MagicMock())
    assert failure.value.status_code == 503
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert failure.value.detail == "AR payment evidence unavailable or unverified"
    qb.get_qbo_ar_payment_match_preview_read_only.assert_awaited_once()
    qb.create_payment.assert_not_awaited()


def test_partial_evidence_may_explicitly_omit_aggregate_counts():
    source = dict(evidence())
    source.update({field: None for field in COUNT_FIELDS})
    source.update(status="not_assessed", reason="complete_source_coverage_unverified")
    valid = ARPaymentPreviewResponse.model_validate({
        **source,
        "source_system": "qbo_sandbox",
        "source_quality": "partial",
        "as_of": "2026-11-20",
    })
    assert all(getattr(valid, field) is None for field in COUNT_FIELDS)
    assert valid.accounting_reconciled is False
    assert valid.financial_write_authorized is False


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", [
    {"linked_invoice_count": 3},
    {"variance_invoice_count": 3},
    {"partially_paid_invoice_count": 3},
    {"linked_invoice_count": 0, "partially_paid_invoice_count": 1},
    {"invoice_count": None, "linked_invoice_count": 0,
     "partially_paid_invoice_count": 1},
])
async def test_contradictory_source_aggregate_counts_fail_closed(contradiction):
    # The comparator's linked/variance/partial counts are invoice subsets.
    # Contradictory aggregated evidence must not be presented as aligned.
    source = dict(evidence())
    source.update(contradiction)
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(return_value=source),
        create_payment=AsyncMock(),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role", return_value="owner"), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb):
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(Response(), user=user(), db=MagicMock())
    assert failure.value.status_code == 503
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert failure.value.detail == "AR payment evidence unavailable or unverified"
    qb.get_qbo_ar_payment_match_preview_read_only.assert_awaited_once()
    qb.create_payment.assert_not_awaited()


def test_invoice_aggregate_subset_boundaries_accept_valid_partial_evidence():
    source = dict(evidence())
    source.update({
        "invoice_count": 2,
        "linked_invoice_count": 2,
        "variance_invoice_count": 2,
        "partially_paid_invoice_count": 2,
    })
    source.update(
        status="variance_detected",
        reason="other_adjustments_or_missing_links_possible",
    )
    accepted = ARPaymentPreviewResponse.model_validate({
        **source,
        "source_system": "qbo_sandbox",
        "source_quality": "partial",
        "as_of": "2026-11-20",
    })
    assert accepted.linked_invoice_count == accepted.invoice_count == 2
    assert accepted.accounting_reconciled is False
    assert accepted.financial_write_authorized is False


@pytest.mark.asyncio
@pytest.mark.parametrize("contradiction", [
    {"status": "aligned_not_certified", "variance_invoice_count": 1},
    {"status": "aligned_not_certified",
     "reason": "other_adjustments_or_missing_links_possible"},
    {"status": "aligned_not_certified", "invoice_count": None},
    {"status": "aligned_not_certified", "unapplied_payment_count": None},
    {"status": "variance_detected", "variance_invoice_count": 0,
     "reason": "other_adjustments_or_missing_links_possible"},
    {"status": "variance_detected", "variance_invoice_count": 1,
     "reason": None},
    {"status": "not_assessed", "reason": "complete_source_coverage_unverified"},
    {"status": "not_assessed", "reason": "other_adjustments_or_missing_links_possible",
     **{name: None for name in COUNT_FIELDS}},
    {"status": "not_assessed", "reason": None,
     **{name: None for name in COUNT_FIELDS}},
])
async def test_contradictory_assessment_status_fails_closed(contradiction):
    source = dict(evidence())
    source.update(contradiction)
    qb = SimpleNamespace(
        _config={"qbo_realm_id": "fixture-sandbox-realm"},
        get_qbo_ar_payment_match_preview_read_only=AsyncMock(return_value=source),
        create_payment=AsyncMock(),
    )
    with patch("app.api.finance_ar_preview.get_settings", return_value=settings()), \
         patch("app.api.finance_ar_preview._current_role", return_value="owner"), \
         patch("app.api.finance_ar_preview.get_qbo_ar_preview_adapter", return_value=qb):
        with pytest.raises(HTTPException) as failure:
            await get_ar_payment_preview(Response(), user=user(), db=MagicMock())
    assert failure.value.status_code == 503
    assert failure.value.headers["Cache-Control"] == "private, no-store"
    assert failure.value.detail == "AR payment evidence unavailable or unverified"
    qb.get_qbo_ar_payment_match_preview_read_only.assert_awaited_once()
    qb.create_payment.assert_not_awaited()


def test_assessed_variance_evidence_accepts_consistent_source():
    source = dict(evidence())
    source.update(
        status="variance_detected",
        reason="other_adjustments_or_missing_links_possible",
        variance_invoice_count=1,
    )
    valid = ARPaymentPreviewResponse.model_validate({
        **source, "source_system": "qbo_sandbox",
        "source_quality": "partial", "as_of": "2026-11-20",
    })
    assert valid.status == "variance_detected"
    assert valid.accounting_reconciled is False
    assert valid.financial_write_authorized is False
