"""Offline tests of REAL-staging probe safety gates (no actual network)."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from scripts.verify_evidence_staging import (
    _CLEAN_PDF, _EICAR, StagingProbeFailure, staging_config,
    validate_bucket_limits, parse_clamd_signature_date,
    probe_real_private_storage, probe_real_scanner, require_anon_denied,
)
from app.services.purchasing_evidence_bytes import MAX_BYTES


def _env(**overrides):
    d = {
        "EVIDENCE_STAGING_CONFIRM": "I_CONFIRM_ISOLATED_STAGING",
        "EVIDENCE_STAGING_PROJECT_REF": "abcdefgh12345678",
        "EVIDENCE_STAGING_SUPABASE_URL": "https://abcdefgh12345678.supabase.co",
        "EVIDENCE_STAGING_SERVICE_ROLE_KEY": "service-test",
        "EVIDENCE_STAGING_ANON_KEY": "anon-test",
    }
    d.update(overrides)
    return d


@pytest.mark.parametrize("changes", [
    {"EVIDENCE_STAGING_CONFIRM": ""},
    {"EVIDENCE_STAGING_PROJECT_REF": ""},
    {"EVIDENCE_STAGING_PROJECT_REF": "prod.service.example.com"},
    {"EVIDENCE_STAGING_SUPABASE_URL": "https://production.supabase.co"},
    {"EVIDENCE_STAGING_SUPABASE_URL": "http://abcdefgh12345678.supabase.co"},
    {"EVIDENCE_STAGING_SUPABASE_URL": "https://abcdefgh12345678.supabase.co/secret"},
    {"EVIDENCE_STAGING_SUPABASE_URL": "https://abcdefgh12345678.supabase.co@attacker.example/"},
    {"EVIDENCE_STAGING_SERVICE_ROLE_KEY": ""},
    {"EVIDENCE_STAGING_ANON_KEY": "service-test"},
])
def test_staging_confirmation_ref_and_credentials_required(changes):
    with pytest.raises(StagingProbeFailure):
        staging_config(_env(**changes))


def test_valid_staging_config_is_explicitly_bound_to_url():
    conf = staging_config(_env())
    assert conf["url"] == "https://abcdefgh12345678.supabase.co"


@pytest.mark.parametrize("overrides", [
    {"public": True},
    {"file_size_limit": None},
    {"file_size_limit": MAX_BYTES + 1},
    {"file_size_limit": 0},
    {"allowed_mime_types": None},
    {"allowed_mime_types": []},
    {"allowed_mime_types": ["application/pdf", "application/x-msdownload"]},
])
def test_bucket_privacy_and_limits_fail_closed(overrides):
    attrs = {"public": False, "file_size_limit": MAX_BYTES,
             "allowed_mime_types": ["application/pdf"]}
    attrs.update(overrides)
    with pytest.raises(StagingProbeFailure):
        validate_bucket_limits(SimpleNamespace(**attrs))


def test_valid_bucket_limits():
    validate_bucket_limits(SimpleNamespace(
        public=False, file_size_limit=MAX_BYTES,
        allowed_mime_types=["application/pdf","image/png","image/jpeg"]))


def _version_string(dt):
    return f"ClamAV 1.4.2/27123/{dt.strftime('%a %b %d %H:%M:%S %Y')}".encode()


def test_real_version_parser_requires_fresh_database():
    now = datetime.now(timezone.utc)
    fresh = now - timedelta(hours=6)
    result = parse_clamd_signature_date(_version_string(fresh), now=now)
    assert abs((result - fresh).total_seconds()) < 1


@pytest.mark.parametrize("reply", [
    b"COMMAND UNAVAILABLE", b"ClamAV abc", b"ClamAV 1.0/0/Tue Oct 13 16:00:00 2026",
    b"ClamAV 1.0/1200/ERROR",
])
def test_invalid_version_does_not_certify_freshness(reply):
    with pytest.raises(StagingProbeFailure):
        parse_clamd_signature_date(reply, now=datetime.now(timezone.utc))


def test_stale_signatures_are_rejected():
    now=datetime.now(timezone.utc)
    with pytest.raises(StagingProbeFailure):
        parse_clamd_signature_date(_version_string(now-timedelta(days=12)), now=now)


def test_real_scanner_probe_checks_both_outcomes_not_just_ping():
    now=datetime.now(timezone.utc)
    checks=[]
    def fake_scan(data, path):
        checks.append((data,path))
        return "infected" if data==_EICAR else "clean"
    with patch("scripts.verify_evidence_staging._clamd_command",
               return_value=_version_string(now)), \
         patch("scripts.verify_evidence_staging._clamav_scan", side_effect=fake_scan):
        result=probe_real_scanner("/run/clamav/clamd.ctl")
    assert result["clean"]=="clean"
    assert result["eicar"]=="infected"
    assert checks==[(_CLEAN_PDF,"/run/clamav/clamd.ctl"),
                    (_EICAR,"/run/clamav/clamd.ctl")]


def test_missing_live_eicar_detection_fails_probe():
    with patch("scripts.verify_evidence_staging._clamd_command",
               return_value=_version_string(datetime.now(timezone.utc))), \
         patch("scripts.verify_evidence_staging._clamav_scan", return_value="clean"):
        with pytest.raises(StagingProbeFailure):
            probe_real_scanner("/run/clamav/clamd.ctl")


class FakeStorage:
    def __init__(self, *, anon_allowed=False):
        self.objects={}
        self.deleted=[]
        self.anon_allowed=anon_allowed
    def get_bucket(self, bucket):
        return SimpleNamespace(public=False, file_size_limit=MAX_BYTES,
                               allowed_mime_types=["application/pdf"])
    def from_(self, bucket):
        return self
    def upload(self, path, file, file_options=None):
        if self.anon_allowed is False and path.endswith("anon.pdf"):
            raise PermissionError("anon denied")
        self.objects[path]=file
    def download(self,path):
        if path not in self.objects:
            raise FileNotFoundError(path)
        return self.objects[path]
    def list(self,prefix):
        if not self.anon_allowed:
            raise PermissionError("anon list denied")
        return []
    def remove(self,paths):
        self.deleted.extend(paths)
        for path in paths:
            self.objects.pop(path,None)


def test_storage_probe_rejects_anon_but_cleans_temp_objects():
    service=SimpleNamespace(storage=FakeStorage())
    anon=SimpleNamespace(storage=FakeStorage())
    out=probe_real_private_storage(service,anon)
    assert out["private"] is True
    assert out["anon_read"] == "denied"
    assert len(service.storage.deleted)==2
    assert not service.storage.objects


def test_storage_probe_fails_and_cleans_when_anon_can_read():
    service=SimpleNamespace(storage=FakeStorage())
    # Reuse service object to simulate overly permissive anonymous client.
    anon=SimpleNamespace(storage=service.storage)
    with pytest.raises(StagingProbeFailure,match="Anonymous client can download"):
        probe_real_private_storage(service,anon)
    assert len(service.storage.deleted)==2
    assert not service.storage.objects


def test_staging_probe_script_never_has_default_write_mode():
    from scripts.verify_evidence_staging import main
    with pytest.raises(SystemExit):
        main([])


def test_storage_outage_is_not_mistaken_for_access_denial():
    with pytest.raises(StagingProbeFailure, match="inconclusive"):
        require_anon_denied(
            lambda: (_ for _ in ()).throw(ConnectionError("staging outage")),
            "download quarantined evidence", allow_not_found=True,
        )


def test_unexpected_404_not_sufficient_for_anon_write_denial():
    with pytest.raises(StagingProbeFailure, match="inconclusive"):
        require_anon_denied(
            lambda: (_ for _ in ()).throw(FileNotFoundError("path")),
            "write into private quarantine",
        )


def test_cleanup_failure_refuses_a_pass():
    class FailedCleanup(FakeStorage):
        def remove(self, paths):
            self.deleted.extend(paths)
            # Purposely leave all objects in staging test store.
    service=SimpleNamespace(storage=FailedCleanup())
    anon=SimpleNamespace(storage=FakeStorage())
    with pytest.raises(StagingProbeFailure,match="cleanup failed"):
        probe_real_private_storage(service,anon)
    assert service.storage.objects


def test_dict_error_without_status_cannot_pass_storage_acl_probe():
    with pytest.raises(StagingProbeFailure):
        require_anon_denied(
            lambda: {"error":"something went wrong"}, "list private objects")
