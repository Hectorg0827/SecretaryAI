"""QBO endpoint isolation: every request is mocked, with no Intuit network traffic."""

from types import SimpleNamespace

import httpx
import pytest

from app.connectors.qb_online import (
    QBOnlineAdapter,
    QBO_PRODUCTION_BASE,
    QBO_SANDBOX_BASE,
)


def _adapter(environment: str | None = None) -> QBOnlineAdapter:
    return QBOnlineAdapter(
        "test-client", "test-secret", "test-access", "test-refresh",
        "test-realm", environment=environment,
    )


@pytest.mark.parametrize(
    ("environment", "base"),
    [
        ("sandbox", QBO_SANDBOX_BASE),
        ("production", QBO_PRODUCTION_BASE),
    ],
)
@pytest.mark.asyncio
async def test_all_qbo_request_paths_use_the_selected_environment(
    monkeypatch, environment, base
):
    """Queries, entity reads, and writes must never leak to the other host."""
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={"QueryResponse": {"totalCount": 0}, "Item": {"Id": "i1"}},
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: real_async_client(transport=transport, **kwargs),
    )
    qb = _adapter(environment)
    await qb._query("SELECT COUNT(*) FROM Customer")
    await qb._get_entity("item", "i1")
    await qb._post("invoice", {"CustomerRef": {"value": "test"}})

    assert [method for method, _ in seen] == ["GET", "GET", "POST"]
    assert len(seen) == 3
    assert all(url.startswith(f"{base}/test-realm/") for _, url in seen)


def test_default_qbo_environment_comes_from_deployment_settings(monkeypatch):
    """Staging callers do not need to remember an explicit environment arg."""
    from app import config

    monkeypatch.setattr(
        config,
        "get_settings",
        lambda: SimpleNamespace(intuit_environment="sandbox"),
    )
    assert _adapter()._base_url == QBO_SANDBOX_BASE


@pytest.mark.parametrize("invalid", ["", "staging", "SANDBOX", "test"])
def test_unrecognized_qbo_environment_fails_closed(invalid):
    with pytest.raises(ValueError, match="Unsupported Intuit environment"):
        _adapter(invalid)
