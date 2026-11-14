"""Header-only insights must not depend on unreadable item quantities."""
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.insights.churn_risk import churn_risk
from app.api.insights.receivables import receivables
from app.api.insights.anomalies import anomalies


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,kwargs", [
    (churn_risk, {"limit": 100}),
    (receivables, {"currency": "USD", "limit": 100}),
    (anomalies, {"lookback_days": 90}),
])
async def test_analytics_source_outage_fails_closed_without_strict_line_reader(endpoint, kwargs):
    adapter = SimpleNamespace(
        get_all_customers=AsyncMock(return_value=[]),
        get_invoice_summaries_last_n_days=AsyncMock(
            side_effect=RuntimeError("synthetic unavailable invoice headers")),
        get_orders_last_n_days=AsyncMock(
            side_effect=AssertionError("strict item-line data not required by this analysis")),
    )
    with pytest.raises(HTTPException) as error:
        await endpoint(user={"company_id": "synthetic-tenant", "role": "owner"},
                       adapter=adapter, **kwargs)
    assert error.value.status_code == 503
    adapter.get_orders_last_n_days.assert_not_awaited()
    adapter.get_invoice_summaries_last_n_days.assert_awaited_once()
