"""Stage 5: synthetic, tenant-scoped employee consent and identity registry."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.intelligence.lola.continuity import Channel
from app.intelligence.lola.identity_registry import (
    GRANTS, LINKS, IdentityAccessDenied, IdentityNotVerified,
    IdentitySourceUnavailable, resolve_verified_case,
)

NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
COMPANY, EMPLOYEE = "tenant-a", "employee-1"
CONTACT, WORK = "contact-17", "design-label-42"
USER = {"company_id": COMPANY, "sub": EMPLOYEE, "role": "sales_rep"}


def make_grants():
    return [
        {
            "company_id": COMPANY, "employee_id": EMPLOYEE, "channel": channel,
            "provider_identity_ref": "internal:" + channel + ":account-1",
            "provider_verification_state": "verified",
            "connector_authorization_state": "connected",
            "employee_consent_at": (NOW - timedelta(days=4)).isoformat(),
            "provider_verified_at": (NOW - timedelta(days=4)).isoformat(),
            "employee_consent_revoked_at": None,
            "authorization_expires_at": (NOW + timedelta(days=20)).isoformat(),
            "source_revision": channel + ":rev1",
        }
        for channel in (Channel.EMAIL.value, Channel.WHATSAPP.value)
    ]


def make_link():
    return {
        "company_id": COMPANY, "employee_id": EMPLOYEE,
        "contact_id": CONTACT, "work_item_id": WORK,
        "email_provider_contact_ref": "provider-email-900",
        "whatsapp_provider_contact_ref": "provider-wa-722",
        "link_verification_state": "verified",
        "verification_source": "provider_plus_employee",
        "employee_confirmed_at": (NOW - timedelta(days=2)).isoformat(),
        "link_verified_at": (NOW - timedelta(days=2)).isoformat(),
        "link_revoked_at": None,
        "verification_expires_at": (NOW + timedelta(days=12)).isoformat(),
        "mapping_revision": "case-map:v2",
    }


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters = []
        self.bound = None
    def select(self, fields):
        self.db.calls.append((self.table, "select", fields))
        return self
    def eq(self, field, value):
        self.filters.append((field, str(value)))
        self.db.calls.append((self.table, "eq", field, str(value)))
        return self
    def limit(self, n):
        self.bound = n
        return self
    def execute(self):
        if self.table == self.db.failing_source:
            raise RuntimeError("SENSITIVE PROVIDER DETAILS DO NOT LEAK")
        rows = deepcopy(self.db.tables[self.table])
        if self.db.ignore_tenant and self.table == LINKS:
            self.filters = [(key, value) for key, value in self.filters if key != "company_id"]
        result = [r for r in rows if all(str(r.get(field)) == value for field, value in self.filters)]
        return SimpleNamespace(data=result[:self.bound])


class FakeDB:
    def __init__(self):
        self.tables = {
            "users": [{
                "id": EMPLOYEE, "company_id": COMPANY,
                "role": "sales_rep", "is_active": True,
            }],
            GRANTS: make_grants(),
            LINKS: [make_link()],
        }
        self.calls = []
        self.failing_source = None
        self.ignore_tenant = False
    def table(self, name):
        assert name in self.tables, "No other service/provider source allowed"
        return Query(self, name)


def resolve(db, user=USER, *, contact_id=CONTACT, work_item_id=WORK):
    return resolve_verified_case(
        user=user, db=db, contact_id=contact_id,
        work_item_id=work_item_id, as_of=NOW,
    )


def test_verified_employee_consent_and_contact_workflow():
    db = FakeDB()
    result = resolve(db)
    assert result.scope.company_id == COMPANY
    assert result.scope.employee_id == EMPLOYEE
    assert result.scope.contact_id == CONTACT
    assert result.scope.work_item_id == WORK
    assert result.scope.employee_consent_verified is True
    assert result.email_contact_ref == "provider-email-900"
    assert result.whatsapp_contact_ref == "provider-wa-722"
    assert result.source == "backend_verified_registry"
    assert result.external_writes_executed is False
    assert len(result.scope.mapping_revision) == 64
    assert db.calls.count((GRANTS, "eq", "company_id", COMPANY)) == 1
    assert db.calls.count((LINKS, "eq", "company_id", COMPANY)) == 1
    assert all(call[1] in ("select", "eq") for call in db.calls)


def test_no_email_vs_whatsapp_guess_when_case_link_missing():
    db = FakeDB()
    db.tables[LINKS] = []
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_duplicate_case_link_is_ambiguous():
    db = FakeDB()
    db.tables[LINKS].append(make_link())
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_duplicate_channel_connection_is_not_accepted():
    db = FakeDB()
    db.tables[GRANTS].append(deepcopy(db.tables[GRANTS][0]))
    with pytest.raises(IdentityNotVerified):
        resolve(db)



def test_malformed_channel_identity_is_denied_not_exception():
    db = FakeDB()
    db.tables[GRANTS][0]["channel"] = ["email"]
    with pytest.raises(IdentityNotVerified):
        resolve(db)

def test_whatsapp_auth_missing_is_not_backfilled_from_email():
    db = FakeDB()
    db.tables[GRANTS] = [db.tables[GRANTS][0]]
    with pytest.raises(IdentityNotVerified):
        resolve(db)


@pytest.mark.parametrize("change", [
    {"employee_consent_at": None},
    {"employee_consent_revoked_at": NOW.isoformat()},
    {"connector_authorization_state": "disconnected"},
    {"provider_verification_state": "pending"},
    {"provider_verified_at": None},
    {"authorization_expires_at": (NOW - timedelta(minutes=1)).isoformat()},
    {"authorization_expires_at": None},
])
def test_revoked_unverified_or_expired_channel_fails_closed(change):
    db = FakeDB()
    db.tables[GRANTS][1].update(change)
    with pytest.raises(IdentityNotVerified):
        resolve(db)


@pytest.mark.parametrize("change", [
    {"link_verification_state": "candidate"},
    {"verification_source": "name_similarity"},
    {"employee_confirmed_at": None},
    {"link_verified_at": None},
    {"link_revoked_at": NOW.isoformat()},
    {"verification_expires_at": (NOW - timedelta(seconds=1)).isoformat()},
    {"verification_expires_at": None},
])
def test_candidate_or_revoked_contact_link_fails_closed(change):
    db = FakeDB()
    db.tables[LINKS][0].update(change)
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_stale_jwt_role_denied_by_current_employee_row():
    db = FakeDB()
    db.tables["users"][0]["role"] = "viewer"
    with pytest.raises(IdentityAccessDenied):
        resolve(db)
    assert all(call[0] == "users" for call in db.calls)


def test_employee_removed_denied():
    db = FakeDB()
    db.tables["users"][0]["is_active"] = False
    with pytest.raises(IdentityAccessDenied):
        resolve(db)
    assert not any(call[0] == LINKS for call in db.calls)


@pytest.mark.parametrize("role", ["viewer", "finance_controller", "pricing_billbacks"])
def test_unauthorized_role_denied_before_registry_access(role):
    db = FakeDB()
    with pytest.raises(IdentityAccessDenied):
        resolve(db, {**USER, "role": role})
    assert not db.calls


def test_link_belongs_to_other_employee():
    db = FakeDB()
    db.tables[LINKS][0]["employee_id"] = "other-user"
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_wrong_tenant_connection_not_reused():
    db = FakeDB()
    db.tables[GRANTS][1]["company_id"] = "tenant-b"
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_wrong_tenant_case_not_visible():
    db = FakeDB()
    db.tables[LINKS][0]["company_id"] = "tenant-b"
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_tenant_filter_defense_in_depth_rejects_leaky_query():
    db = FakeDB()
    db.ignore_tenant = True
    db.tables[LINKS][0]["company_id"] = "tenant-b"
    with pytest.raises(IdentitySourceUnavailable):
        resolve(db)


@pytest.mark.parametrize("table", ["users", GRANTS, LINKS])
def test_source_outage_fails_closed_and_does_not_expose_provider_error(table):
    db = FakeDB()
    db.failing_source = table
    with pytest.raises(IdentitySourceUnavailable) as info:
        resolve(db)
    assert "SENSITIVE PROVIDER" not in str(info.value)



def test_missing_registry_table_fails_closed_before_query_execution():
    class MissingTableDb(FakeDB):
        def table(self, name):
            if name == LINKS:
                raise RuntimeError("SENSITIVE MISSING TABLE EXCEPTION")
            return super().table(name)
    with pytest.raises(IdentitySourceUnavailable) as info:
        resolve(MissingTableDb())
    assert "SENSITIVE" not in str(info.value)


def test_registry_query_construction_failure_is_sanitized():
    class BrokenRegistryDb(FakeDB):
        def table(self, name):
            if name == GRANTS:
                class Broken:
                    def select(self, _columns):
                        raise RuntimeError("SENSITIVE PRIVILEGE ERROR")
                return Broken()
            return super().table(name)
    with pytest.raises(IdentitySourceUnavailable) as info:
        resolve(BrokenRegistryDb())
    assert "SENSITIVE" not in str(info.value)

def test_source_revision_changes_scope_fingerprint():
    db = FakeDB()
    original = resolve(db).source_fingerprint
    db.tables[GRANTS][0]["source_revision"] = "email:rev2"
    new = resolve(db)
    assert original != new.source_fingerprint
    assert new.scope.mapping_revision == new.source_fingerprint


def test_link_revision_changes_scope_fingerprint():
    db = FakeDB()
    original = resolve(db).source_fingerprint
    db.tables[LINKS][0]["mapping_revision"] = "case-map:v3"
    assert resolve(db).source_fingerprint != original


def test_future_employee_consent_fails_closed():
    db = FakeDB()
    db.tables[GRANTS][0]["employee_consent_at"] = (NOW + timedelta(hours=1)).isoformat()
    with pytest.raises(IdentityNotVerified):
        resolve(db)


def test_naive_provider_timestamp_is_rejected():
    db = FakeDB()
    db.tables[GRANTS][0]["provider_verified_at"] = "2026-11-01T13:00:00"
    with pytest.raises(IdentitySourceUnavailable):
        resolve(db)


def test_untrusted_external_selector_cannot_expand_scope():
    db = FakeDB()
    with pytest.raises(IdentityNotVerified):
        resolve(db, contact_id="someone-else")
    with pytest.raises(IdentityNotVerified):
        resolve(db, work_item_id="other-project")


def test_invalid_as_of_never_creates_a_fabricated_consent():
    db = FakeDB()
    with pytest.raises(ValueError):
        resolve_verified_case(
            user=USER, db=db, contact_id=CONTACT, work_item_id=WORK,
            as_of=datetime(2026, 11, 15),
        )
