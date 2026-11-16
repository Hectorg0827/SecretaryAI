"""QBO-only AR acceptance adapter tests (synthetic, no network).

Verify finance preview cannot accidentally refresh Gmail/Outlook OAuth or
expose unrelated employee connector credentials while preparing a QBO read.
No actual provider, database, or financial mutation occurs.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.api.deps import get_qbo_ar_preview_adapter

COMPANY = "synthetic-company"
OWNER = {"sub": "synthetic-owner", "company_id": COMPANY}


def company(**updates):
    payload = {
        "id": COMPANY,
        "qb_type": "online",
        "qbo_realm_id": "sandbox-realm-1",
        "qbo_access_token": "ENCRYPTED_ACCESS",
        "qbo_refresh_token": "ENCRYPTED_REFRESH",
        # These must not be passed to the QBO-only adapter or refreshed.
        "google_access_token": "ENCRYPTED_GMAIL_ACCESS_CANARY",
        "google_refresh_token": "ENCRYPTED_GMAIL_REFRESH_CANARY",
        "ms_access_token": "ENCRYPTED_OUTLOOK_ACCESS_CANARY",
        "ms_refresh_token": "ENCRYPTED_OUTLOOK_REFRESH_CANARY",
        "ms_client_secret": "ENCRYPTED_OUTLOOK_CLIENT_SECRET",
        "gmail_credentials": {"token": "CANARY_GMAIL_CREDENTIAL"},
        "shopify_access_token": "CANARY_SHOPIFY",
    }
    payload.update(updates)
    return payload


def settings():
    return SimpleNamespace(
        intuit_client_id="sandbox-intuit-client",
        intuit_client_secret="sandbox-intuit-secret",
        secret_key="SYNTHETIC_DECRYPT_KEY",
        secretaryai_deployment_env="staging",
        intuit_environment="sandbox",
    )


def _decrypt(ciphertext, _key):
    return {
        "ENCRYPTED_ACCESS": "SYNTHETIC_QBO_ACCESS",
        "ENCRYPTED_REFRESH": "SYNTHETIC_QBO_REFRESH",
    }[ciphertext]


def test_owner_ar_preview_loads_only_company_scoped_qbo_credentials():
    db = MagicMock()
    fake_adapter = MagicMock()
    with patch("app.api.deps._load_company", return_value=company()) as load, \
         patch("app.api.deps.get_settings", return_value=settings()), \
         patch("app.api.deps.decrypt", side_effect=_decrypt) as decrypt, \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter",
               return_value=fake_adapter) as create, \
         patch("app.auth.oauth.refresh_google_token_if_needed") as gmail_refresh:
        assert get_qbo_ar_preview_adapter(user=OWNER, db=db) is fake_adapter
    load.assert_called_once_with(COMPANY, db)
    assert decrypt.call_count == 2
    assert [x.args[0] for x in decrypt.call_args_list] == [
        "ENCRYPTED_ACCESS", "ENCRYPTED_REFRESH",
    ]
    cfg = create.call_args.args[0]
    assert set(cfg) == {
        "id", "company_id", "qb_type", "qbo_client_id",
        "qbo_client_secret", "qbo_access_token", "qbo_refresh_token",
        "_qbo_expected_refresh_ciphertext", "qbo_realm_id",
    }
    assert cfg["id"] == COMPANY
    assert cfg["company_id"] == COMPANY
    assert cfg["qbo_realm_id"] == "sandbox-realm-1"
    assert cfg["qbo_access_token"] == "SYNTHETIC_QBO_ACCESS"
    assert cfg["qbo_refresh_token"] == "SYNTHETIC_QBO_REFRESH"
    assert cfg["_qbo_expected_refresh_ciphertext"] == "ENCRYPTED_REFRESH"
    assert all("CANARY" not in repr(x) for x in cfg.values())
    gmail_refresh.assert_not_called()
    fake_adapter.set_db.assert_called_once_with(db)


@pytest.mark.parametrize("broken", [
    {"id": "another-tenant"},
    {"qb_type": "desktop"},
    {"qb_type": None},
    {"qbo_realm_id": ""},
    {"qbo_access_token": None},
    {"qbo_refresh_token": None},
    {"qbo_access_token": " "},
])
def test_mismatched_or_unconnected_company_fails_before_token_decrypt(broken):
    with patch("app.api.deps._load_company", return_value=company(**broken)), \
         patch("app.api.deps.get_settings", return_value=settings()), \
         patch("app.api.deps.decrypt") as decrypt, \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as factory:
        with pytest.raises(PermissionError):
            get_qbo_ar_preview_adapter(user=OWNER, db=MagicMock())
    factory.assert_not_called()
    decrypt.assert_not_called()


@pytest.mark.parametrize("identity", [
    {},
    {"company_id": ""},
    {"company_id": 123},
    None,
])
def test_unverified_user_identity_cannot_read_any_company(identity):
    with patch("app.api.deps._load_company") as load:
        with pytest.raises(PermissionError):
            get_qbo_ar_preview_adapter(user=identity, db=MagicMock())
    load.assert_not_called()


def test_failed_qbo_decryption_never_constructs_adapter_or_email_refresh():
    with patch("app.api.deps._load_company", return_value=company()), \
         patch("app.api.deps.get_settings", return_value=settings()), \
         patch("app.api.deps.decrypt", side_effect=ValueError("invalid QBO token")), \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as factory, \
         patch("app.auth.oauth.refresh_google_token_if_needed") as gmail_refresh:
        with pytest.raises(ValueError):
            get_qbo_ar_preview_adapter(user=OWNER, db=MagicMock())
    factory.assert_not_called()
    gmail_refresh.assert_not_called()


def test_invalid_decrypted_qbo_credentials_never_build_adapter():
    with patch("app.api.deps._load_company", return_value=company()), \
         patch("app.api.deps.get_settings", return_value=settings()), \
         patch("app.api.deps.decrypt", return_value=""), \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as factory:
        with pytest.raises(PermissionError):
            get_qbo_ar_preview_adapter(user=OWNER, db=MagicMock())
    factory.assert_not_called()


@pytest.mark.parametrize("environment,intuit", [
    ("production", "sandbox"), ("local", "sandbox"),
    ("staging", "production"),
])
def test_qbo_only_adapter_cannot_touch_company_outside_staging_sandbox(
    environment, intuit,
):
    disallowed = settings()
    disallowed.secretaryai_deployment_env = environment
    disallowed.intuit_environment = intuit
    with patch("app.api.deps.get_settings", return_value=disallowed), \
         patch("app.api.deps._load_company") as load, \
         patch("app.api.deps.decrypt") as decrypt, \
         patch("app.connectors.unified_adapter.UnifiedDataAdapter") as factory:
        with pytest.raises(PermissionError, match="staging sandbox"):
            get_qbo_ar_preview_adapter(user=OWNER, db=MagicMock())
    load.assert_not_called()
    decrypt.assert_not_called()
    factory.assert_not_called()


def test_real_adapter_constructor_never_builds_unrelated_connectors():
    # Execute the real UnifiedDataAdapter constructor, mocking each actual
    # connector class (rather than mocking the complete adapter factory).
    # No Gmail/Outlook/Shopify or Computer Use provider network calls occur.
    db = MagicMock()
    with patch("app.api.deps._load_company", return_value=company()), \
         patch("app.api.deps.get_settings", return_value=settings()), \
         patch("app.api.deps.decrypt", side_effect=_decrypt), \
         patch("app.connectors.unified_adapter.QBOnlineAdapter") as online, \
         patch("app.connectors.unified_adapter.GmailConnector") as gmail, \
         patch("app.connectors.unified_adapter.OutlookConnector") as outlook, \
         patch("app.connectors.unified_adapter.ShopifyConnector") as shopify, \
         patch("app.connectors.unified_adapter.GoogleSheetsConnector") as sheets, \
         patch("app.connectors.unified_adapter.ShipTrackConnector") as shipping, \
         patch("app.connectors.unified_adapter.ComputerUseEngine") as computer:
        adapter = get_qbo_ar_preview_adapter(user=OWNER, db=db)
    assert online.call_count == 1
    assert online.call_args.kwargs["realm_id"] == "sandbox-realm-1"
    assert online.call_args.kwargs["access_token"] == "SYNTHETIC_QBO_ACCESS"
    assert online.call_args.kwargs["refresh_token"] == "SYNTHETIC_QBO_REFRESH"
    assert callable(online.call_args.kwargs["on_token_refresh"])
    assert adapter._qb is online.return_value
    assert adapter._db is db
    assert adapter._gmail is None
    assert adapter._outlook is None
    assert adapter._shopify is None
    assert adapter._sheets is None
    gmail.assert_not_called()
    outlook.assert_not_called()
    shopify.assert_not_called()
    sheets.assert_not_called()
    assert shipping.call_count == 1
    assert computer.call_count == 1
    # Optional support classes receive no other provider credentials.
    assert all("CANARY" not in repr(x) for x in
               computer.call_args.kwargs["company_config"].values())
