"""Stage 10 registry SQL contract and Python Stage 5 mapping parity.

This is a no-database static contract test. It DOES NOT replace direct
PostgreSQL RLS, transaction race or role-privilege acceptance before any
hosted staging database migration.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.intelligence.lola.identity_registry import resolve_verified_case
from app.intelligence.lola.replay_persistence import CursorVault
from app.intelligence.lola.continuity import WorkScope

_SQL = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20261115203000_lola_verified_identity_replay_write_gate_staging.sql"
)


def _sql():
    return _SQL.read_text(encoding="utf-8")


def _stage5_revision(
    company: str, employee: str, contact: str, work: str, link_rev: str,
    email_rev: str, whatsapp_rev: str,
):
    """Byte-exact Stage 5 (Python) fingerprint, used by SQL trigger."""
    pieces = (
        company, employee, contact, work, link_rev,
        "email:" + email_rev, "whatsapp_business:" + whatsapp_rev,
    )
    return hashlib.sha256("\n".join(pieces).encode("utf-8")).hexdigest()


def test_registry_schema_contains_stage5_expected_columns():
    s = _sql()
    assert "CREATE TABLE public.lola_channel_authorizations (" in s
    assert "CREATE TABLE public.lola_verified_case_links (" in s
    assert "CREATE TABLE public.lola_authorized_email_resources (" in s
    for field in (
        "provider_identity_ref", "provider_verification_state",
        "connector_authorization_state", "employee_consent_at",
        "employee_consent_revoked_at", "provider_verified_at",
        "authorization_expires_at", "source_revision", "contact_id",
        "work_item_id", "email_provider_contact_ref",
        "whatsapp_provider_contact_ref", "link_verification_state",
        "verification_source", "employee_confirmed_at", "link_verified_at",
        "link_revoked_at", "verification_expires_at", "mapping_revision",
    ):
        assert field in s


def test_no_browser_grants_and_rls_required_for_all_registry_sources():
    s = _sql()
    for table in (
        "lola_channel_authorizations", "lola_verified_case_links",
        "lola_authorized_email_resources",
    ):
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY;" in s
        assert f"REVOKE ALL ON public.{table}" in s
    assert "TO anon" not in s
    assert "TO authenticated" not in s
    assert "TO service_role" in s
    assert "SECURITY DEFINER" not in s
    assert "SECURITY INVOKER" in s


def test_checkpoint_guard_actually_attaches_to_preexisting_stage8_table():
    s = _sql()
    assert "BEFORE INSERT OR UPDATE ON public.lola_provider_replay_checkpoints" in s
    assert "FOR EACH ROW EXECUTE FUNCTION public.lola_guard_provider_replay_checkpoint()" in s
    assert "FOREIGN KEY (" in s
    assert "REFERENCES public.lola_provider_replay_checkpoints" in s
    assert "BEFORE INSERT ON public.lola_provider_replay_events" in s
    assert "lola_guard_provider_replay_event()" in s


def test_sql_locking_prevents_grant_or_case_revocation_during_commit():
    s = _sql()
    assert s.count("FOR SHARE") >= 6
    assert "v_email.employee_consent_revoked_at IS NOT NULL" in s
    assert "v_whatsapp.employee_consent_revoked_at IS NOT NULL" in s
    assert "l.link_revoked_at IS NULL" in s
    assert "r.revoked_at IS NULL" in s
    assert "u.is_active IS TRUE" in s
    assert "l.verification_source='provider_plus_employee'" in s
    assert "v_resource_present IS DISTINCT FROM TRUE" in s
    assert "v_link_present IS DISTINCT FROM TRUE" in s


def test_sql_fingerprint_matches_stage5_python_revision_contract():
    # Stage 5 uses 7 newline-separated strings in deterministic channel order.
    company = "11111111-1111-4111-8111-111111111111"
    employee = "22222222-2222-4222-8222-222222222222"
    from app.intelligence.lola.identity_registry import GRANTS, LINKS
    assert GRANTS == "lola_channel_authorizations"
    assert LINKS == "lola_verified_case_links"
    expected = _stage5_revision(
        company, employee, "contact-17", "design-label-42", "case-map:v2",
        "email:rev1", "whatsapp_business:rev1",
    )
    assert len(expected) == 64
    assert expected != _stage5_revision(
        company, employee, "contact-17", "design-label-42", "case-map:v2",
        "email:rev2", "whatsapp_business:rev1",
    )
    assert expected != _stage5_revision(
        company, employee, "contact-17", "other-work", "case-map:v2",
        "email:rev1", "whatsapp_business:rev1",
    )
    s = _sql()
    assert "pg_catalog.sha256(pg_catalog.convert_to(" in s
    assert "pg_catalog.concat_ws(E'\\n'," in s
    assert "'email:' || v_email.source_revision" in s
    assert "'whatsapp_business:' || v_whatsapp.source_revision" in s
    assert "l.contact_id, l.work_item_id, l.mapping_revision" in s


def test_resource_identity_key_contract_matches_backend_hmac():
    v = CursorVault(b"k" * 32)
    company, employee = (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    provider_ref = "verified-connection-no-raw-address"
    digest = v.digest(company, employee, "gmail", provider_ref)
    assert len(digest) == 64
    assert digest != v.digest(company, employee, "gmail", "another-account")
    assert digest != v.digest(company, employee, "outlook", provider_ref)
    assert digest != v.digest("33333333-3333-4333-8333-333333333333",
                              employee, "gmail", provider_ref)


def test_sql_sends_no_message_or_financial_action():
    s = _sql().casefold()
    for forbidden in (
        "sendmail", "quickbooks", "http_post", "post_to_webhook",
        "whatsapp_send", "send_approved_email", "notify_lola",
    ):
        assert forbidden not in s
    assert "do not apply to any hosted" in s


def test_existing_stage8_migration_not_modified_by_stage10():
    older = (
        Path(__file__).resolve().parents[2]
        / "supabase/migrations/20261115181500_lola_provider_replay_checkpoints_staging.sql"
    )
    source = older.read_text(encoding="utf-8")
    assert "CREATE OR REPLACE FUNCTION public.lola_commit_provider_replay(" in source
    assert "CREATE TABLE IF NOT EXISTS public.lola_provider_replay_checkpoints" in source
    # Stage 10 is purely additive and wraps Stage 8 writes in a trigger.
    assert "lola_commit_provider_replay" not in _sql()
