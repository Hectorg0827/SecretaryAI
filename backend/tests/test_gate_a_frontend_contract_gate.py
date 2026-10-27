"""Synthetic unit tests for the disposable five-workstream client contract gate.

This does not prove customer UI behavior; it proves the CI sentinel detects
missing canonical briefing integration and nullable account types.
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_gate_a_frontend_contract.py"
spec = spec_from_file_location("check_gate_a_frontend_contract", SCRIPT)
gate = module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(gate)


WEB = """
export interface DashboardSummary {
  accounts: { healthy: number; at_risk: number; } | null;
}
export interface BriefingTodayResponse { complete_digest: false; }
export const api = {
  briefing: { today: () => get<BriefingTodayResponse>('/api/briefing/today') }
};
"""
UI = """
const briefing = await api.briefing.today();
const warning = briefing.complete_digest ? 'Complete' : 'Partial';
const provenance = briefing.source_quality + briefing.source_coverage;
const unavailable = 'Source unavailable';
"""
MOBILE = """
export interface DashboardSummary {
  accounts: { at_risk: number; } | null;
}
"""


def test_complete_synthetic_wire_contract_is_accepted():
    assert gate.check_contract(WEB, UI, MOBILE) == []


def test_unintegrated_briefing_is_rejected():
    messages = gate.check_contract(
        WEB, "const data = await Promise.allSettled([sales(), feed()]);",
        MOBILE,
    )
    assert any("must consume api.briefing.today()" in msg for msg in messages)
    assert any("Promise.allSettled" in msg for msg in messages)


def test_untagged_dashboard_account_fields_fail_both_clients():
    messages = gate.check_contract(
        WEB.replace("| null", ""),
        UI,
        MOBILE.replace("| null", ""),
    )
    assert any("Web DashboardSummary.accounts" in msg for msg in messages)
    assert any("Mobile DashboardSummary.accounts" in msg for msg in messages)


def test_api_without_versioned_briefing_contract_fails():
    messages = gate.check_contract(
        "export const api = {dashboard: {summary: () => get('/api/dashboard/summary')}};",
        UI, MOBILE,
    )
    assert any("must request /api/briefing/today" in msg for msg in messages)
    assert any("typed BriefingToday" in msg for msg in messages)


def test_claim_of_loaded_briefing_without_quality_warnings_fails():
    messages = gate.check_contract(
        WEB, "const result = await api.briefing.today();", MOBILE,
    )
    assert any("complete_digest" in msg for msg in messages)
    assert any("source_quality" in msg for msg in messages)
    assert any("source_coverage" in msg for msg in messages)


def test_exact_backend_type_name_briefing_today_response_v1_is_accepted():
    typed_web = WEB.replace("BriefingTodayResponse", "BriefingTodayResponseV1")
    assert gate.check_contract(typed_web, UI, MOBILE) == []


def test_named_nullable_alias_and_reversed_union_are_accepted():
    web_named = WEB.replace(
        "accounts: { healthy: number; at_risk: number; } | null;",
        "accounts: AccountHealth | null;",
    )
    mobile_reversed = MOBILE.replace(
        "accounts: { at_risk: number; } | null;",
        "accounts: null | AccountHealth;",
    )
    assert gate.check_contract(web_named, UI, mobile_reversed) == []
