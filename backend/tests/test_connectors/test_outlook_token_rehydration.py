"""Persisted Outlook token rehydration: API and background task factories.

Synthetic ciphertext only; no database, Graph API, or customer credentials.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.utils.encryption import encrypt


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
        "ms_client_secret": "fixture-client-secret",
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
