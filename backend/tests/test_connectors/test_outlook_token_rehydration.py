"""Persisted Outlook token rehydration: API and background task factories.

Synthetic ciphertext only; no database, Graph API, or customer credentials.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.utils.encryption import encrypt
from app.utils.encryption import decrypt


def _settings():
    return SimpleNamespace(
        secret_key="test-only-key-never-used-in-production",
        intuit_client_id="test-qbo-client",
        intuit_client_secret="test-qbo-secret",
        conductor_api_key="",
    )


def _company(settings):
    return {
        "id": "tenant-fixture",
        "ms_tenant_id": "tenant-fixture",
        "ms_client_id": "outlook-client",
        "ms_client_secret": encrypt("fixture-client-secret", settings.secret_key),
        "ms_access_token": encrypt("rotated-access-token", settings.secret_key),
        "ms_refresh_token": encrypt("rotated-refresh-token", settings.secret_key),
    }


def test_api_adapter_never_receives_company_mailbox_tokens():
    """Company OAuth credentials have no verified personal owner."""
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    db = MagicMock()
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company), \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as adapter_type:
        adapter = deps.get_adapter(
            user={"sub": "employee-a", "company_id": "tenant-fixture"}, db=db
        )
    config = adapter_type.call_args.args[0]
    for key in ("ms_access_token", "ms_refresh_token", "ms_client_secret", "ms_tenant_id"):
        assert key not in config
    assert "_private_mailbox_owner_id" not in config
    adapter.set_db.assert_called_once_with(db)



def test_background_worker_never_receives_company_mailbox_tokens():
    from tasks import base

    settings = _settings()
    company = _company(settings)
    with patch.object(base, "settings", settings), \
         patch.object(base, "UnifiedDataAdapter") as adapter_type:
        base.build_adapter(company)
    config = adapter_type.call_args.args[0]
    for key in ("ms_access_token", "ms_refresh_token", "ms_client_secret", "ms_tenant_id"):
        assert key not in config



def test_api_adapter_never_decrypts_unowned_corrupt_outlook_refresh():
    """An invalid company ciphertext is not even consulted by personal Lola."""
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    company["ms_refresh_token"] = encrypt("unexpected", "different-fixture-key")
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company):
        adapter = deps.get_adapter(
            user={"sub": "employee-a", "company_id": "tenant-fixture"}, db=MagicMock()
        )
    assert adapter._outlook is None



def test_api_adapter_never_exposes_company_mailbox_client_secret():
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company):
        adapter = deps.get_adapter(
            user={"sub": "employee-a", "company_id": "tenant-fixture"}, db=MagicMock()
        )
    assert adapter._outlook is None



@pytest.mark.asyncio
async def test_outlook_refresh_uses_tenant_and_ciphertext_generation_cas():
    """Disconnects and new consents cannot be overwritten by old Graph workers."""
    from app.connectors.unified_adapter import UnifiedDataAdapter

    settings = _settings()
    prior = encrypt("prior-refresh", settings.secret_key)
    row = {"id": "tenant-fixture", "ms_refresh_token": prior, "ms_access_token": "old"}

    class Query:
        def __init__(self):
            self.filters = {}
            self.updates = {}
        def table(self, name):
            assert name == "companies"
            self.filters = {}
            return self
        def update(self, data):
            self.updates = data
            return self
        def eq(self, key, value):
            self.filters[key] = value
            return self
        def execute(self):
            if all(row.get(key) == value for key, value in self.filters.items()):
                row.update(self.updates)
                return SimpleNamespace(data=[dict(row)])
            return SimpleNamespace(data=[])

    db = Query()
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._config = {"id": "tenant-fixture", "_ms_expected_refresh_ciphertext": prior}
    with patch("app.config.get_settings", return_value=settings), \
         patch("app.utils.qbo_token_store.create_qbo_client", return_value=db):
        await adapter._persist_outlook_tokens("rotated-access", "rotated-refresh")
        assert decrypt(row["ms_access_token"], settings.secret_key) == "rotated-access"
        assert decrypt(row["ms_refresh_token"], settings.secret_key) == "rotated-refresh"
        assert adapter._config["_ms_expected_refresh_ciphertext"] == row["ms_refresh_token"]
        row["ms_refresh_token"] = None  # An owner disconnected while a worker refreshed.
        with pytest.raises(RuntimeError, match="could not be persisted"):
            await adapter._persist_outlook_tokens("stale-access", "stale-refresh")
    assert row["ms_access_token"] != "stale-access"
    assert row["ms_refresh_token"] is None


@pytest.mark.parametrize("factory", ["api", "worker"])
def test_corrupt_company_outlook_client_secret_never_leaks_to_personal_adapters(factory):
    settings = _settings()
    company = _company(settings)
    company["ms_client_secret"] = encrypt("leak-canary-secret", "different-fixture-key")
    if factory == "api":
        from app.api import deps
        with patch.object(deps, "get_settings", return_value=settings), \
             patch.object(deps, "_load_company", return_value=company):
            adapter = deps.get_adapter(
                user={"sub": "employee-a", "company_id": "tenant-fixture"}, db=MagicMock()
            )
        assert adapter._outlook is None
    else:
        from tasks import base
        with patch.object(base, "settings", settings), \
             patch.object(base, "UnifiedDataAdapter") as adapter_type:
            base.build_adapter(company)
        config = adapter_type.call_args.args[0]
        assert "ms_client_secret" not in config
        assert "ms_access_token" not in config
