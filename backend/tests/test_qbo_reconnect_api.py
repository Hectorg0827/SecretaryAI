"""Reconnect state is visible to the UI without exposing QBO credentials."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.api.settings import get_integrations


def _db(row):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[row])
    )
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,realm_id,connected,reconnect",
    [
        ("connected", "sandbox-realm", True, False),
        ("disconnected", "sandbox-realm", False, True),
        ("disconnected", None, False, False),
    ],
)
async def test_integrations_exposes_explicit_qbo_reconnect_state(
    status, realm_id, connected, reconnect,
):
    db = _db({
        "qb_type": "online",
        "qb_connection_status": status,
        "qbo_realm_id": realm_id,
        "qbo_connected_at": "2026-11-10T12:00:00Z",
    })
    result = await get_integrations(user={"company_id": "tenant-1"}, db=db)
    assert result["quickbooks_online"]["connected"] is connected
    assert result["quickbooks_online"]["needs_reconnect"] is reconnect
    assert "qbo_access_token" not in str(result)
    assert "qbo_refresh_token" not in str(result)
