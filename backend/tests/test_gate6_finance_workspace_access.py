"""Gate 6A: finance segregation before employee role activation."""
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Response

from app.api.finance_workspace import (
    FinanceWorkspaceResponse, _current_role, build_finance_workspace,
    get_finance_workspace,
)
from app.auth.rbac import ROLE_PERMISSIONS
from app.domain.finance_access import finance_scopes_for_role


OBSERVED = datetime(2026, 11, 10, 5, tzinfo=timezone.utc)


def snapshot():
    return {"generated_at": OBSERVED.isoformat(), "payload": {
        "source_read_status": "completed", "source_quality": "partial",
        "finance_document_coverage": {
            "as_of": "2026-11-10", "currency_verified": False,
            "ledger_reconciled": False,
            "ar": {"status": "observed", "source_quality": "partial", "reason": None,
                   "headers_examined": 20, "open_count": 8, "overdue_count": 3,
                   "due_date_missing_count": 1},
            "ap": {"status": "observed", "source_quality": "partial", "reason": None,
                   "headers_examined": 14, "open_count": 5, "overdue_count": 2,
                   "due_date_missing_count": 0},
        },
        "sensitive_vendor": "DO NOT EXPOSE", "sensitive_customer": "DO NOT EXPOSE",
        "unverified_total": 10999999,
    }}


@pytest.mark.parametrize("role,scopes", [
    ("owner", {"ar", "ap", "ledger", "pricing"}),
    ("manager", {"ar", "ap", "ledger", "pricing"}),
    ("finance_controller", {"ar", "ap", "ledger"}),
    ("ar_specialist", {"ar"}),
    ("ap_specialist", {"ap"}),
    ("pricing_billbacks", {"pricing"}),
])
def test_exact_role_areas_and_no_sensitive_data(role, scopes):
    result = build_finance_workspace(role=role, snapshot=snapshot(), now=OBSERVED)
    assert set(result["sections"]) == scopes
    assert set(result["authorized_scopes"]) == scopes
    assert FinanceWorkspaceResponse.model_validate(result).source_quality == "partial"
    assert "DO NOT EXPOSE" not in str(result)
    assert "10999999" not in str(result)
    assert result["currency_verified"] is False
    assert result["ledger_reconciled"] is False


@pytest.mark.parametrize("role", ["back_office", "sales_rep", "viewer", "unknown", ""])
def test_other_roles_denied(role):
    assert not finance_scopes_for_role(role)
    assert "view_finance_workspace" not in ROLE_PERMISSIONS.get(role, set())
    with pytest.raises(PermissionError):
        build_finance_workspace(role=role, snapshot=snapshot(), now=OBSERVED)


@pytest.mark.parametrize("role", ["finance_controller", "ar_specialist", "ap_specialist", "pricing_billbacks"])
def test_department_roles_have_no_broad_access(role):
    assert ROLE_PERMISSIONS[role] == {"view_finance_workspace"}
    assert not ROLE_PERMISSIONS[role].intersection({
        "view_all", "view_financials", "run_reports", "approve_actions",
        "trigger_actions", "manage_billing", "manage_users", "view_customers",
        "view_inventory", "draft_purchase_orders",
    })


def test_missing_and_unavailable_sources_remain_unknown_not_zero():
    data = snapshot()
    del data["payload"]["finance_document_coverage"]
    result = build_finance_workspace(role="finance_controller", snapshot=data, now=OBSERVED)
    assert result["sections"]["ar"]["status"] == "not_assessed"
    assert result["sections"]["ar"]["open_count"] is None
    assert result["sections"]["ap"]["status"] == "not_assessed"
    assert result["sections"]["ledger"]["status"] == "not_assessed"
    assert result["sections"].get("pricing") is None


def test_pricing_does_not_claim_unsupported_billback_totals():
    result = build_finance_workspace(role="pricing_billbacks", snapshot=snapshot(), now=OBSERVED)
    assert result["sections"]["pricing"]["status"] == "not_assessed"
    assert result["sections"]["pricing"]["headers_examined"] is None


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(source_quality="verified"),
    lambda p: p["finance_document_coverage"]["ar"].update(customer_name="Sensitive"),
    lambda p: p["finance_document_coverage"].update(currency_verified=True),
])
def test_incomplete_or_tampered_source_refused(mutation):
    data = snapshot()
    mutation(data["payload"])
    with pytest.raises(ValueError):
        build_finance_workspace(role="ar_specialist", snapshot=data, now=OBSERVED)


def test_stale_finance_snapshot_fails_closed():
    with pytest.raises(ValueError, match="stale"):
        build_finance_workspace(role="manager", snapshot=snapshot(),
                                now=datetime(2026, 11, 12, tzinfo=timezone.utc))


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []

    def select(self, _):
        return self

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, _):
        return self

    def execute(self):
        return type("Result", (), {"data": self.rows})()


class _Db:
    def __init__(self, rows):
        self.query = _Query(rows)

    def table(self, name):
        assert name == "users"
        return self.query


def test_current_db_role_and_tenant_must_match_token():
    company, user_id = str(uuid4()), str(uuid4())
    user = {"company_id": company, "sub": user_id, "role": "ar_specialist"}
    row = {"id": user_id, "company_id": company, "role": "ar_specialist", "is_active": True}
    db = _Db([row])
    assert _current_role(db, user) == "ar_specialist"
    assert ("id", user_id) in db.query.filters
    assert ("company_id", company) in db.query.filters
    for change in ({"role": "owner"}, {"company_id": str(uuid4())}, {"is_active": False}):
        with pytest.raises(HTTPException) as exc:
            _current_role(_Db([{**row, **change}]), user)
        assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        _current_role(_Db([row, row]), user)
    with pytest.raises(HTTPException):
        _current_role(_Db([]), user)


@pytest.mark.asyncio
async def test_production_never_reads_finance_workspace_data():
    with patch("app.api.finance_workspace.get_settings") as settings:
        settings.return_value.secretaryai_deployment_env = "production"
        with pytest.raises(HTTPException) as exc:
            await get_finance_workspace(
                Response(), user={"role": "owner", "sub": "a", "company_id": "c"}, db=None,
            )
    assert exc.value.status_code == 404
