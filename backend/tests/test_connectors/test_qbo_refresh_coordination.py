"""Offline QBO distributed-refresh race and reconnect regression coverage."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

import fakeredis
import fakeredis.aioredis
import httpx
import pytest

from app.connectors.qb_online import QBOnlineAdapter, QBO_OAUTH_TOKEN_URL
from app.connectors.unified_adapter import UnifiedDataAdapter
from app.utils import qbo_refresh_coordination, qbo_token_store
from app.utils.qbo_token_store import QBOReconnectRequired


class _Query:
    def __init__(self, database):
        self.database = database
        self.mode = "select"
        self.filters = []
        self.values = None

    def select(self, *_):
        self.mode = "select"
        return self

    def update(self, values):
        self.mode = "update"
        self.values = values
        return self

    def eq(self, name, value):
        self.filters.append((name, value))
        return self

    def execute(self):
        with self.database.guard:
            matched = all(self.database.row.get(k) == v for k, v in self.filters)
            if not matched:
                return SimpleNamespace(data=[])
            if self.mode == "update":
                if self.database.reject_writes:
                    return SimpleNamespace(data=[])
                self.database.row.update(self.values)
            return SimpleNamespace(data=[dict(self.database.row)])


class _Database:
    def __init__(self):
        from threading import Lock
        self.guard = Lock()
        self.reject_writes = False
        self.row = {
            "id": "tenant-1",
            "qb_type": "online",
            "qb_connection_status": "connected",
            "qbo_realm_id": "realm-1",
            "qbo_access_token": "cipher:old-access",
            "qbo_refresh_token": "cipher:old-refresh",
            "qbo_token_expires_at": None,
        }

    def table(self, name):
        assert name == "companies"
        return _Query(self)


class _HTTP:
    def __init__(self, response_code=200):
        self.response_code = response_code
        self.refresh_requests = 0
        self.api_requests = 0
        self.timeouts = []

    def client(self, **options):
        self.timeouts.append(options.get("timeout"))
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, *, headers, **kwargs):
        self.api_requests += 1
        await asyncio.sleep(0)  # both caller requests observe the old bearer
        code = 401 if headers["Authorization"] == "Bearer old-access" else 200
        return httpx.Response(
            code,
            json={"QueryResponse": {"Customer": [{"Id": "1"}]}} if code == 200 else {},
            request=httpx.Request("GET", url),
        )

    async def post(self, url, **_):
        assert url == QBO_OAUTH_TOKEN_URL
        self.refresh_requests += 1
        await asyncio.sleep(0)
        if self.response_code == 400:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "do-not-log-response"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600},
            request=httpx.Request("POST", url),
        )


@pytest.fixture
def isolated_qbo(monkeypatch):
    db = _Database()
    server = fakeredis.FakeServer()
    settings = SimpleNamespace(
        redis_url="redis://fixture.invalid:6379/0",
        supabase_url="https://fixture.invalid",
        supabase_service_role_key="fixture",
        secret_key="fixture-secret",
        intuit_environment="sandbox",
    )
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(qbo_refresh_coordination, "create_qbo_client", lambda *_: db)
    monkeypatch.setattr(qbo_token_store, "create_qbo_client", lambda *_: db)
    monkeypatch.setattr(qbo_token_store, "encrypt", lambda value, _: "cipher:" + value)
    monkeypatch.setattr(qbo_refresh_coordination, "decrypt", lambda value, _: value.removeprefix("cipher:"))
    monkeypatch.setattr(
        qbo_refresh_coordination.async_redis, "from_url",
        lambda *args, **kwargs: fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
    )
    monkeypatch.setattr(
        qbo_refresh_coordination.redis, "from_url",
        lambda *args, **kwargs: fakeredis.FakeRedis(server=server, decode_responses=True),
    )
    return db


def _adapter(*, expected="cipher:old-refresh"):
    cfg = {
        "id": "tenant-1",
        "qbo_realm_id": "realm-1",
        "qbo_access_token": "old-access",
        "qbo_refresh_token": "old-refresh",
        "_qbo_expected_refresh_ciphertext": expected,
    }
    unified = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
    unified._config = cfg
    adapter = QBOnlineAdapter(
        "client-id", "client-secret", "old-access", "old-refresh",
        "realm-1", on_token_refresh=unified._persist_qbo_tokens,
    )
    adapter._qbo_refresh_config = cfg
    return adapter, cfg


@pytest.mark.asyncio
async def test_two_simultaneous_401s_rotate_once_and_both_queries_succeed(isolated_qbo, caplog):
    http = _HTTP()
    first, first_cfg = _adapter()
    second, second_cfg = _adapter()
    with patch("httpx.AsyncClient", side_effect=http.client), caplog.at_level(logging.DEBUG):
        results = await asyncio.gather(
            first._query("SELECT * FROM Customer"),
            second._query("SELECT * FROM Customer"),
        )
    assert results[0] == results[1]
    assert http.refresh_requests == 1
    assert first._access_token == second._access_token == "new-access"
    assert first_cfg["_qbo_expected_refresh_ciphertext"] == "cipher:new-refresh"
    assert second_cfg["_qbo_expected_refresh_ciphertext"] == "cipher:new-refresh"
    assert 15 in http.timeouts
    assert isolated_qbo.row["qb_connection_status"] == "connected"
    _assert_redacted(caplog)


@pytest.mark.asyncio
async def test_stale_cas_loser_adopts_winners_saved_tokens_without_intuit(isolated_qbo, caplog):
    isolated_qbo.row.update({
        "qbo_access_token": "cipher:new-access",
        "qbo_refresh_token": "cipher:new-refresh",
    })
    loser, cfg = _adapter()
    http = _HTTP()
    with patch("httpx.AsyncClient", side_effect=http.client), caplog.at_level(logging.DEBUG):
        await loser._refresh_access_token()
    assert http.refresh_requests == 0
    assert loser._access_token == "new-access"
    assert loser._refresh_token == "new-refresh"
    assert cfg["_qbo_expected_refresh_ciphertext"] == "cipher:new-refresh"
    _assert_redacted(caplog)


@pytest.mark.asyncio
async def test_invalid_grant_marks_existing_disconnected_state_and_never_retries(isolated_qbo, caplog):
    adapter, _ = _adapter()
    http = _HTTP(response_code=400)
    with patch("httpx.AsyncClient", side_effect=http.client), caplog.at_level(logging.DEBUG):
        with pytest.raises(QBOReconnectRequired, match="QuickBooks needs to be reconnected"):
            await adapter._refresh_access_token()
    assert http.refresh_requests == 1
    assert isolated_qbo.row["qb_connection_status"] == "disconnected"
    assert isolated_qbo.row["qbo_refresh_token"] is None
    assert isolated_qbo.row["qbo_access_token"] is None
    assert adapter._access_token == "old-access"
    _assert_redacted(caplog)


@pytest.mark.asyncio
async def test_post_intuit_cas_failure_raises_and_does_not_expose_unsaved_tokens(isolated_qbo, caplog):
    isolated_qbo.reject_writes = True
    adapter, _ = _adapter()
    http = _HTTP()
    with patch("httpx.AsyncClient", side_effect=http.client), caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError, match="QBO token refresh could not be persisted"):
            await adapter._refresh_access_token()
    assert http.refresh_requests == 1
    assert adapter._access_token == "old-access"
    assert adapter._refresh_token == "old-refresh"
    assert isolated_qbo.row["qbo_refresh_token"] == "cipher:old-refresh"
    _assert_redacted(caplog)


def _assert_redacted(caplog):
    log_lines = "\n".join(record.getMessage() for record in caplog.records)
    for forbidden in (
        "old-access", "new-access", "old-refresh", "new-refresh",
        "cipher:", "do-not-log-response", "client-secret",
    ):
        assert forbidden not in log_lines
