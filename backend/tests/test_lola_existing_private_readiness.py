"""Readiness of EXISTING Lola private staging sources, never verified silence."""
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.auth.rbac import get_current_user
from app.main import app
from app.intelligence.lola.existing_private_readiness import (
    EMAIL, WHATSAPP, CONTACTS, inspect_existing_readiness,
)
from app.intelligence.lola.identity_registry import (
    IdentityAccessDenied, IdentitySourceUnavailable,
)
from test_lola_identity_registry import FakeDB, NOW, USER, COMPANY, EMPLOYEE

CONTACT_ID = "123e4567-e89b-12d3-a456-426614174090"


def email_rows():
    return [{
        "company_id": COMPANY, "user_id": EMPLOYEE,
        "provider": "gmail", "connected_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "access_token": "SENSITIVE TOKEN MUST NOT BE SELECTED",
        "refresh_token": "SENSITIVE REFRESH MUST NOT BE SELECTED",
    }]


def whatsapp_rows():
    return [{
        "company_id": COMPANY, "user_id": EMPLOYEE, "status": "active",
        "phone_number_id": "wa-business-phone-id",
        "waba_id": "meta-business-account-id",
        "meta_verified_at": NOW.isoformat(),
        "employee_consented_at": NOW.isoformat(),
        "employee_revoked_at": None, "history_sync_consented": True,
        "policy_clearance_id": "policy-approved",
        "updated_at": NOW.isoformat(),
    }]


def contact_rows():
    return [{
        "id": CONTACT_ID, "company_id": COMPANY, "user_id": EMPLOYEE,
        "identity_verified": True,
        "display_name": "Sensitive contact name must stay private",
        "email": "private@example.com", "phone": "+1-555-0100",
    }]


class PrivateDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.tables[EMAIL] = email_rows()
        self.tables[WHATSAPP] = whatsapp_rows()
        self.tables[CONTACTS] = contact_rows()


def review(db=None, contact_id=None):
    return inspect_existing_readiness(user=USER, db=db or PrivateDB(), contact_id=contact_id)


def test_existing_channels_are_only_metadata_not_proof_of_no_reply():
    db = PrivateDB()
    result = review(db)
    assert result["source"] == "existing_employee_private_tables"
    assert result["email_state"] == "single_connection_recorded"
    assert result["whatsapp_state"] == "existing_consent_and_ownership_metadata_present"
    assert result["provider_message_coverage_verified"] is False
    assert result["cross_channel_work_item_verified"] is False
    assert result["eligible_for_follow_up_engine"] is False
    assert result["proactive_notifications_enabled_by_this_check"] is False
    assert result["external_writes_executed"] is False
    assert all(step[1] in ("select", "eq") for step in db.calls)


def test_contact_flag_is_candidate_only_not_provider_identity_proof():
    from uuid import UUID
    result = review(contact_id=UUID(CONTACT_ID))
    assert result["contact_state"] == "existing_identity_flag_recorded_not_provider_verified"
    assert result["eligible_for_follow_up_engine"] is False


def test_private_tokens_contacts_and_message_content_are_not_requested():
    db = PrivateDB()
    result = review(db, contact_id=__import__("uuid").UUID(CONTACT_ID))
    source_fields = [entry[2] for entry in db.calls if entry[1] == "select"]
    for selected in source_fields:
        for secret in ("access_token", "refresh_token", "display_name", "email,",
                       "phone,", "summary", "source_ref"):
            assert secret not in selected
    for marker in ("SENSITIVE TOKEN", "Sensitive contact", "private@example.com", "555-0100"):
        assert marker not in str(result)


def test_missing_whatsapp_connection_cannot_be_treated_as_accepted():
    db = PrivateDB()
    db.tables[WHATSAPP] = []
    result = review(db)
    assert result["whatsapp_state"] == "no_employee_whatsapp_connection_recorded"
    assert result["eligible_for_follow_up_engine"] is False


def test_revoked_whatsapp_consent_prohibits_readiness():
    db = PrivateDB()
    db.tables[WHATSAPP][0]["employee_revoked_at"] = NOW.isoformat()
    result = review(db)
    assert result["whatsapp_state"] == "connection_pending_or_consent_incomplete"


def test_no_email_connected_at_does_not_mean_no_mail_exists():
    db = PrivateDB()
    db.tables[EMAIL][0]["connected_at"] = None
    result = review(db)
    assert result["email_state"] == "no_active_email_connection_recorded"
    assert result["eligible_for_follow_up_engine"] is False


def test_foreign_contact_not_disclosed():
    from uuid import UUID
    db = PrivateDB()
    db.tables[CONTACTS][0]["company_id"] = "foreign-tenant"
    result = review(db, contact_id=UUID(CONTACT_ID))
    assert result["contact_state"] == "candidate_only_or_not_found"
    assert "private@example.com" not in str(result)


@pytest.mark.parametrize("source", [EMAIL, WHATSAPP, CONTACTS])
def test_missing_private_table_fails_unavailable(source):
    from uuid import UUID
    db = PrivateDB()
    db.failing_source = source
    with pytest.raises(IdentitySourceUnavailable):
        review(db, contact_id=UUID(CONTACT_ID))


def test_denied_role_cannot_read_user_private_channel_metadata():
    db = PrivateDB()
    user = {**USER, "role": "viewer"}
    with pytest.raises(IdentityAccessDenied):
        inspect_existing_readiness(user=user, db=db)
    assert db.calls == []


def test_disabled_employee_cannot_read_private_metadata():
    db = PrivateDB()
    db.tables["users"][0]["is_active"] = False
    with pytest.raises(IdentityAccessDenied):
        review(db)
    assert all(entry[0] == "users" for entry in db.calls)


def test_multiple_whatsapp_connections_invalid():
    db = PrivateDB()
    db.tables[WHATSAPP].append(deepcopy(db.tables[WHATSAPP][0]))
    with pytest.raises(IdentitySourceUnavailable):
        review(db)


def test_malformed_email_provider_not_accepted():
    db = PrivateDB()
    db.tables[EMAIL][0]["provider"] = ["gmail"]
    with pytest.raises(IdentitySourceUnavailable):
        review(db)


@pytest.fixture
def client():
    db = PrivateDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(USER)
    yield TestClient(app), db
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_api_readiness_is_owner_scoped_and_never_claims_reminder_authority(client):
    web, db = client
    result = web.get("/api/lola/chief-of-staff/continuity/readiness")
    assert result.status_code == 200, result.text
    assert result.headers.get("cache-control") == "private, no-store"
    assert result.json()["eligible_for_follow_up_engine"] is False
    assert "access_token" not in result.text
    assert "SENSITIVE" not in result.text


def test_api_denied_current_role(client):
    web, db = client
    db.tables["users"][0]["role"] = "viewer"
    response = web.get("/api/lola/chief-of-staff/continuity/readiness")
    assert response.status_code == 403
    assert not any(t in (EMAIL, WHATSAPP) for t, *_ in db.calls)


def test_api_missing_table_is_503_not_success(client):
    web, db = client
    db.failing_source = WHATSAPP
    response = web.get("/api/lola/chief-of-staff/continuity/readiness")
    assert response.status_code == 503
    assert "SENSITIVE" not in response.text


def test_api_invalid_uuid_is_422(client):
    web, db = client
    response = web.get("/api/lola/chief-of-staff/continuity/readiness?contact_id=not-uuid")
    assert response.status_code == 422
