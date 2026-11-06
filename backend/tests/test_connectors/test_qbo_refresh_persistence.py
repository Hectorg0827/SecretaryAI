"""Fixture-only QBO rotation checks (never contact Intuit or Supabase)."""

from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from postgrest import SyncPostgrestClient

from app.api.auth import qbo_callback
from app.connectors.qb_online import QBOnlineAdapter
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.utils.qbo_token_store import persist_rotated_qbo_tokens
from tasks.base import _refresh_qbo_if_needed
from tasks.qbo_token_refresh import refresh_expiring_tokens


def _db_with_updated_ids(ids):
    db = MagicMock()
    query = db.table.return_value.update.return_value
    query.eq.return_value = query
    query.execute.return_value = SimpleNamespace(data=ids)
    return db


def _rotate(db, **overrides):
    kwargs = dict(
        company_id="tenant-1",
        expected_refresh_ciphertext="old-encrypted-refresh",
        realm_id="realm-1",
        access_token="new-access",
        refresh_token="new-refresh",
        expires_in=1800,
        secret_key="test-key",
    )
    kwargs.update(overrides)
    with patch("app.utils.qbo_token_store.encrypt", side_effect=lambda value, _: f"encrypted:{value}"):
        return persist_rotated_qbo_tokens(db, **kwargs)


def _offline_postgrest(updated_rows):
    """Use the real pinned PostgREST request builder without network access."""
    db = SyncPostgrestClient("https://fixture.invalid/rest/v1")
    sent = []

    def respond(method, path, **options):
        sent.append((method, path, options))
        return httpx.Response(
            200,
            json=updated_rows,
            request=httpx.Request("PATCH", "https://fixture.invalid/rest/v1/companies"),
        )

    return db, sent, respond


def test_rotated_tokens_really_persist_using_installed_postgrest_builder():
    db, sent, respond = _offline_postgrest([{"id": "tenant-1"}])
    with patch.object(db.session, "request", side_effect=respond):
        assert _rotate(db) == "encrypted:new-refresh"
    assert len(sent) == 1
    method, path, options = sent[0]
    assert str(method).upper().endswith("PATCH")
    assert path == "/companies"
    assert options["headers"]["Prefer"] == "return=representation"
    assert options["params"]["id"] == "eq.tenant-1"
    assert options["params"]["qbo_refresh_token"] == "eq.old-encrypted-refresh"


def test_rotated_tokens_really_reject_no_match_with_installed_postgrest_builder():
    db, _sent, respond = _offline_postgrest([])
    with patch.object(db.session, "request", side_effect=respond):
        with pytest.raises(RuntimeError, match="matched no current connected company"):
            _rotate(db)


@pytest.mark.asyncio
async def test_qbo_reconnect_callback_persists_with_installed_postgrest_builder():
    db, sent, respond = _offline_postgrest([{"id": "tenant-1"}])
    fresh = {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_in": 3600}
    with patch("app.api.auth._state_pop", return_value="tenant-1"), \
         patch("app.api.auth.exchange_code_for_tokens", new_callable=AsyncMock, return_value=fresh), \
         patch("supabase.create_client", return_value=db), \
         patch("app.api.auth.encrypt", side_effect=lambda value, _: f"encrypted:{value}"), \
         patch.object(db.session, "request", side_effect=respond):
        result = await qbo_callback(code="fixture-code", state="fixture-state", realm_id="realm-1")
    assert result.status_code == 307
    assert result.headers["location"].endswith("/settings?qbo=connected")
    assert sent[0][2]["headers"]["Prefer"] == "return=representation"
    assert sent[0][2]["json"]["qbo_token_refreshed_at"]


def test_qbo_rotation_requires_original_ciphertext_and_current_company_row():
    db = _db_with_updated_ids([{"id": "tenant-1"}])
    assert _rotate(db) == "encrypted:new-refresh"

    query = db.table.return_value.update.return_value
    assert ("id", "tenant-1") in [c.args for c in query.eq.call_args_list]
    assert ("qbo_refresh_token", "old-encrypted-refresh") in [c.args for c in query.eq.call_args_list]
    assert ("qbo_realm_id", "realm-1") in [c.args for c in query.eq.call_args_list]
    assert ("qb_connection_status", "connected") in [c.args for c in query.eq.call_args_list]
    assert ("qb_type", "online") in [c.args for c in query.eq.call_args_list]
    query.select.assert_not_called()

    persisted = db.table.return_value.update.call_args.args[0]
    assert persisted["qbo_access_token"] == "encrypted:new-access"
    assert persisted["qbo_refresh_token"] == "encrypted:new-refresh"
    assert datetime.fromisoformat(persisted["qbo_token_refreshed_at"]).tzinfo is not None
    assert datetime.fromisoformat(persisted["qbo_token_expires_at"]) > datetime.now(timezone.utc)


def test_qbo_rotation_rejects_reconnect_race_or_an_already_rotated_token():
    db = _db_with_updated_ids([])  # PostgREST CAS matches no rows
    with pytest.raises(RuntimeError, match="matched no current connected company"):
        _rotate(db)
    with pytest.raises(RuntimeError, match="prior credential"):
        _rotate(db, expected_refresh_ciphertext=None)


@pytest.mark.asyncio
async def test_qbo_connector_persists_before_exposing_rotated_credentials():
    qbo = QBOnlineAdapter("client", "secret", "old-access", "old-refresh", "realm-1")
    callback = AsyncMock(side_effect=RuntimeError("fixture CAS conflict"))
    qbo._on_token_refresh = callback
    response = MagicMock()
    response.json.return_value = {"access_token": "new-access", "refresh_token": "new-refresh"}
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.return_value = response
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(RuntimeError, match="CAS conflict"):
            await qbo._refresh_access_token()
    assert qbo._access_token == "old-access"
    assert qbo._refresh_token == "old-refresh"


@pytest.mark.asyncio
async def test_api_rotation_moves_cas_version_after_successful_persistence():
    adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    adapter._config = {
        "id": "tenant-1", "qbo_realm_id": "realm-1",
        "_qbo_expected_refresh_ciphertext": "old-encrypted-refresh",
    }
    db = _db_with_updated_ids([{"id": "tenant-1"}])
    settings = SimpleNamespace(
        supabase_url="https://example.invalid", supabase_service_role_key="fake",
        secret_key="fake",
    )
    with patch("supabase.create_client", return_value=db), \
         patch("app.config.get_settings", return_value=settings), \
         patch("app.utils.qbo_token_store.encrypt", side_effect=lambda value, _: f"encrypted:{value}"):
        await adapter._persist_qbo_tokens("first-access", "first-refresh")
        assert adapter._config["_qbo_expected_refresh_ciphertext"] == "encrypted:first-refresh"
        await adapter._persist_qbo_tokens("second-access", "second-refresh")
    assert ("qbo_refresh_token", "encrypted:first-refresh") in [
        c.args for c in db.table().update().eq.call_args_list
    ]


def test_task_proactive_rotation_checks_cas_before_updating_credentials():
    company = {
        "id": "tenant-1", "qbo_realm_id": "realm-1",
        "qbo_refresh_token": "old-encrypted-refresh",
        "qbo_token_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    cfg = {"qbo_access_token": "old-access", "qbo_refresh_token": "old-refresh"}
    new_tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
    with patch("app.auth.oauth.refresh_qbo_token", new_callable=AsyncMock, return_value=new_tokens), \
         patch("app.utils.qbo_token_store.persist_rotated_qbo_tokens", return_value="new-encrypted-refresh") as store:
        _refresh_qbo_if_needed(company, cfg, MagicMock())
    assert store.call_args.kwargs["expected_refresh_ciphertext"] == "old-encrypted-refresh"
    assert store.call_args.kwargs["realm_id"] == "realm-1"
    assert cfg["qbo_refresh_token"] == "new-refresh"
    assert cfg["_qbo_expected_refresh_ciphertext"] == "new-encrypted-refresh"


def test_task_proactive_rotation_fails_closed_on_cas_conflict():
    company = {
        "id": "tenant-1", "qbo_refresh_token": "old-encrypted-refresh",
        "qbo_token_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    }
    cfg = {"qbo_access_token": "old-access", "qbo_refresh_token": "old-refresh"}
    new_tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
    with patch("app.auth.oauth.refresh_qbo_token", new_callable=AsyncMock, return_value=new_tokens), \
         patch("app.utils.qbo_token_store.persist_rotated_qbo_tokens", side_effect=RuntimeError("CAS conflict")):
        with pytest.raises(RuntimeError, match="proactive token refresh failed"):
            _refresh_qbo_if_needed(company, cfg, MagicMock())
    assert cfg["qbo_refresh_token"] == "old-refresh"


@pytest.mark.parametrize("matched,expected_refreshed,expected_errors", [
    (True, 1, 0),
    (False, 0, 1),
])
def test_scheduled_qbo_refresh_uses_cas_and_reports_conflicts(
    matched, expected_refreshed, expected_errors
):
    company = {
        "id": "tenant-1", "qbo_realm_id": "realm-1",
        "qbo_refresh_token": "old-encrypted-refresh",
        "qbo_token_expires_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
    }
    db = MagicMock()
    db.table().select().eq().not_.is_().execute.return_value = SimpleNamespace(data=[company])
    new_tokens = {"access_token": "new-access", "refresh_token": "new-refresh"}
    failure = RuntimeError("fixture CAS conflict") if not matched else None
    with patch("tasks.qbo_token_refresh.task_lock", return_value=nullcontext(True)), \
         patch("tasks.qbo_token_refresh.get_supabase", return_value=db), \
         patch("app.utils.encryption.decrypt", return_value="old-refresh"), \
         patch("app.auth.oauth.refresh_qbo_token", new_callable=AsyncMock, return_value=new_tokens), \
         patch("app.utils.qbo_token_store.persist_rotated_qbo_tokens", side_effect=failure) as store:
        summary = refresh_expiring_tokens.run()
    assert summary["refreshed"] == expected_refreshed
    assert summary["errors"] == expected_errors
    assert store.call_args.kwargs["expected_refresh_ciphertext"] == "old-encrypted-refresh"
    assert store.call_args.kwargs["realm_id"] == "realm-1"


def test_staging_qbo_timestamp_schema_patch_is_tracked_separately_from_baseline():
    repo_root = Path(__file__).resolve().parents[3]
    patch_file = repo_root / "staging_supabase/patches/20261106030845_qbo_token_refresh_timestamp.sql"
    sql = patch_file.read_text()
    assert "ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ" in sql
    assert "public.companies" in sql
    standard = repo_root / "supabase/migrations/20261106030900_qbo_token_refresh_timestamp.sql"
    assert "ADD COLUMN IF NOT EXISTS qbo_token_refreshed_at TIMESTAMPTZ" in standard.read_text()
