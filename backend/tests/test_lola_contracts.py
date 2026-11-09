"""Published contracts stay in sync with code; mocks are valid; status rules hold."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.lola.contracts import (
    CONTRACT_MODELS, ActionReceipt, LolaRequest, LolaResponse, LolaStatus, combine_statuses,
)
from app.lola.mocks import MockLola, example_responses

ROOT = Path(__file__).resolve().parents[2]


def test_published_json_contracts_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "export_lola_contracts.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_typescript_mirror_lists_every_model_and_status():
    ts = (ROOT / "frontend" / "src" / "lola" / "contracts.ts").read_text()
    for model in CONTRACT_MODELS:
        assert f"interface {model.__name__}" in ts, model.__name__
    for status in LolaStatus:
        assert f'"{status.value}"' in ts, status.value
    for field in LolaResponse.model_fields:
        assert f"  {field}:" in ts, field


def test_examples_cover_every_client_state_and_round_trip():
    ex = example_responses()
    assert {"answered", "partial", "denied", "unavailable", "pending_approval", "completed", "failed",
            "high_risk"} <= set(ex)
    for resp in ex.values():
        assert LolaResponse.model_validate_json(resp.model_dump_json()) == resp


def test_only_verified_receipts_are_completed():
    done = example_responses()["completed"].receipts[0]
    assert done.status == LolaStatus.COMPLETED and done.verified and done.external_reference
    pending = example_responses()["pending_approval"].receipts[0]
    assert not pending.verified


def test_request_rejects_unknown_fields():
    with pytest.raises(Exception):
        LolaRequest.model_validate({"message": "hi", "company_id": "other"})


@pytest.mark.parametrize("statuses,expected", [
    ([], LolaStatus.UNAVAILABLE),
    ([LolaStatus.ANSWERED, LolaStatus.ANSWERED], LolaStatus.ANSWERED),
    ([LolaStatus.ANSWERED, LolaStatus.DENIED], LolaStatus.PARTIAL),
    ([LolaStatus.ANSWERED, LolaStatus.UNAVAILABLE], LolaStatus.PARTIAL),
    ([LolaStatus.DENIED, LolaStatus.DENIED], LolaStatus.DENIED),
    ([LolaStatus.DENIED, LolaStatus.UNAVAILABLE], LolaStatus.UNAVAILABLE),
    ([LolaStatus.PENDING_APPROVAL, LolaStatus.ANSWERED], LolaStatus.PARTIAL),
])
def test_combined_status_never_overstates_success(statuses, expected):
    assert combine_statuses(statuses) == expected


async def test_mock_routes_by_keyword():
    m = MockLola()
    u = {"sub": "u", "company_id": "c"}
    assert (await m.handle(u, LolaRequest(message="payroll?"))).status == LolaStatus.DENIED
    assert (await m.handle(u, LolaRequest(message="email acme"))).status == LolaStatus.PENDING_APPROVAL
    assert (await m.handle(u, LolaRequest(message="hello"))).status == LolaStatus.ANSWERED


def test_receipt_contract_has_no_success_without_verification_field():
    assert "verified" in ActionReceipt.model_fields
