"""Defense-in-depth: raw logistics provenance events are server-only.

A Data API client must never be allowed to claim a carrier/customs source, or
bypass the role-scoped projections that redact supplier/customer/financial data.
"""
from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "supabase/migrations/20261109000011_logistics_journey.sql"
)


def test_logistics_event_ledger_has_default_denied_client_roles():
    sql = MIGRATION.read_text(encoding="utf-8").upper()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON TABLE PUBLIC.LOGISTICS_EVENTS FROM PUBLIC, ANON, AUTHENTICATED;" in sql
    assert "FOR ALL USING" not in sql
    assert "CREATE POLICY" not in sql
    assert "UNIQUE (COMPANY_ID, IDEMPOTENCY_KEY)" in sql
    assert "BEFORE UPDATE ON LOGISTICS_EVENTS" in sql
    assert "CONFIRMED_BY <> ''" in sql
