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


def test_api_adapter_decrypts_persisted_outlook_rotation_before_graph_setup():
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    db = MagicMock()
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company), \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as adapter_type:
        adapter = deps.get_adapter(user={"company_id": "tenant-fixture"}, db=db)

    config = adapter_type.call_args.args[0]
    assert config["ms_access_token"] == "rotated-access-token"
    assert config["ms_refresh_token"] == "rotated-refresh-token"
    assert config["ms_client_secret"] == "fixture-client-secret"
    assert company["ms_access_token"] != config["ms_access_token"]
    assert company["ms_refresh_token"] != config["ms_refresh_token"]
    adapter.set_db.assert_called_once_with(db)


def test_worker_adapter_decrypts_persisted_outlook_rotation_before_graph_setup():
    from tasks import base

    settings = _settings()
    company = _company(settings)
    with patch.object(base, "settings", settings), \
         patch.object(base, "UnifiedDataAdapter") as adapter_type:
        base.build_adapter(company)

    config = adapter_type.call_args.args[0]
    assert config["ms_access_token"] == "rotated-access-token"
    assert config["ms_refresh_token"] == "rotated-refresh-token"
    assert config["ms_client_secret"] == "fixture-client-secret"
    assert company["ms_access_token"] != config["ms_access_token"]


def test_api_adapter_rejects_corrupt_outlook_refresh_ciphertext():
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    company["ms_refresh_token"] = encrypt("unexpected", "different-fixture-key")
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company):
        with pytest.raises(ValueError):
            deps.get_adapter(user={"company_id": "tenant-fixture"}, db=MagicMock())


def test_api_adapter_gives_graph_connector_the_decrypted_client_secret():
    from app.api import deps

    settings = _settings()
    company = _company(settings)
    with patch.object(deps, "get_settings", return_value=settings), \
         patch.object(deps, "_load_company", return_value=company):
        adapter = deps.get_adapter(user={"company_id": "tenant-fixture"}, db=MagicMock())

    # This value is posted to Microsoft's token endpoint on every refresh.
    assert adapter._outlook._client_secret == "fixture-client-secret"
    assert adapter._outlook._refresh_token == "rotated-refresh-token"


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
def test_corrupt_outlook_client_secret_fails_closed_without_leaking(factory):
    settings = _settings()
    company = _company(settings)
    company["ms_client_secret"] = encrypt("leak-canary-secret", "different-fixture-key")
    with pytest.raises(ValueError) as err:
        if factory == "api":
            from app.api import deps
            with patch.object(deps, "get_settings", return_value=settings), \
                 patch.object(deps, "_load_company", return_value=company):
                deps.get_adapter(user={"company_id": "tenant-fixture"}, db=MagicMock())
        else:
            from tasks import base
            with patch.object(base, "settings", settings), \
                 patch.object(base, "UnifiedDataAdapter"):
                base.build_adapter(company)
    assert "leak-canary-secret" not in str(err.value)
    assert company["ms_client_secret"] not in str(err.value)
