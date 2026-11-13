"""Staging background token refresh failures must never persist provider secrets."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from tasks.base import _refresh_qbo_if_needed, build_adapter


def test_failed_qbo_refresh_never_logs_provider_error_or_tenant_id(caplog):
    company = {
        "id": "SENSITIVE_TENANT_CANARY",
        "qbo_token_expires_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
        "qbo_refresh_token": "ENCRYPTED_REFRESH_CANARY",
    }
    cfg = {"qbo_refresh_token": "SECRET_REFRESH_CANARY"}
    with patch(
        "app.auth.oauth.refresh_qbo_token",
        new=AsyncMock(side_effect=RuntimeError("SECRET_PROVIDER_URL_CANARY")),
    ):
        with pytest.raises(RuntimeError, match="QBO proactive token refresh failed"):
            _refresh_qbo_if_needed(company, cfg, object())
    assert "QBO token refresh failed" in caplog.text
    for secret in (
        "SECRET_PROVIDER_URL_CANARY", "SENSITIVE_TENANT_CANARY",
        "ENCRYPTED_REFRESH_CANARY", "SECRET_REFRESH_CANARY",
    ):
        assert secret not in caplog.text


def test_failed_google_refresh_never_logs_provider_exception(caplog):
    company = {
        "id": "SENSITIVE_COMPANY",
        "google_access_token": "FAKE_ACCESS_CIPHERTEXT",
        "google_refresh_token": "FAKE_REFRESH_CIPHERTEXT",
    }
    with patch("app.utils.encryption.decrypt", side_effect=lambda value, key: value), \
         patch("app.auth.oauth.refresh_google_token_if_needed", side_effect=RuntimeError("SECRET_GOOGLE_URL_CANARY")), \
         patch("tasks.base.UnifiedDataAdapter", side_effect=lambda config: config):
        adapter_config = build_adapter(company)
    assert adapter_config["gmail_credentials"]["refresh_token"] == "FAKE_REFRESH_CIPHERTEXT"
    assert "Google token refresh failed in task" in caplog.text
    assert "SECRET_GOOGLE_URL_CANARY" not in caplog.text
    assert "SENSITIVE_COMPANY" not in caplog.text
