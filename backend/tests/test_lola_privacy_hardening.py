"""Regressions for private-Lola provenance and migration safety."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import private_continuity


def test_manual_events_cannot_impersonate_verified_provider_messages(monkeypatch):
    monkeypatch.setattr(
        private_continuity, "_contact",
        lambda *_args, **_kwargs: {"id": "owner-private-contact", "identity_verified": True},
    )
    event = private_continuity.NewEvent(
        contact_id="owner-private-contact", project_key="Joan label artwork",
        channel="email", direction="outbound", kind="draft_sent",
        occurred_at=datetime.now(timezone.utc),
        source_ref="gmail:forged-id",
    )
    with pytest.raises(HTTPException) as blocked:
        private_continuity.record_private_event(
            event, user={"sub": "employee-1", "company_id": "company-1"}, db=object()
        )
    assert blocked.value.status_code == 422
    assert "Provider source" in blocked.value.detail


def test_migration_contains_owning_user_keys_and_valid_postgresql_delimiter():
    file = (Path(__file__).resolve().parents[1] / "supabase" / "migrations" /
            "20261113213000_lola_employee_private_intelligence.sql")
    sql = file.read_text(encoding="utf-8")
    assert "DO $lola$" in sql and "END\n$lola$;" in sql
    assert "FOREIGN KEY (company_id, user_id, contact_id)" in sql
    assert "FOREIGN KEY (company_id, user_id, event_id)" in sql
    assert "REVOKE ALL ON lola_private_events FROM anon, authenticated" in sql
    assert "origin text NOT NULL DEFAULT 'manual'" in sql


def test_gmail_oauth_never_grants_employee_drive_or_sheets_access():
    src = (Path(__file__).resolve().parents[1] / "app" / "auth" / "oauth.py")
    text = src.read_text(encoding="utf-8")
    grant = text.split("PERSONAL_GMAIL_SCOPES =", 1)[1].split("\n", 1)[0]
    assert "gmail.modify" in grant
    assert "spreadsheets" not in grant
    assert "drive.file" not in grant



@pytest.mark.parametrize("environment,enabled,allowed", [
    ("production", True, False),
    ("staging", False, False),
    ("", True, False),
    ("staging", True, True),
])
def test_employee_private_pilot_requires_explicit_staging_gate(
    monkeypatch, environment, enabled, allowed
):
    from types import SimpleNamespace
    from app.api import private_continuity, auth
    settings = SimpleNamespace(
        secretaryai_deployment_env=environment,
        lola_private_intelligence_enabled=enabled,
    )
    monkeypatch.setattr(private_continuity, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    for guard in (private_continuity.require_private_pilot, auth._require_employee_mail_pilot):
        if allowed:
            guard()
        else:
            with pytest.raises(HTTPException) as blocked:
                guard()
            assert blocked.value.status_code == 404
