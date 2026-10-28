"""The chat account overview must not hide customers after a credit balance."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.data_summarizer import summarize_accounts_overview
from app.api.chat import _fetch_data_for_intent


@pytest.mark.asyncio
async def test_credit_balance_does_not_truncate_chat_account_overview():
    adapter = SimpleNamespace(
        get_all_customers=AsyncMock(return_value=[
            SimpleNamespace(qb_id="account-1", name="First Account", balance=0),
            SimpleNamespace(qb_id="account-2", name="Credit Account", balance=-25.50),
            SimpleNamespace(qb_id="account-3", name="Last Account", balance=15),
        ]),
        get_orders_last_n_days=AsyncMock(return_value=[]),
    )

    result = await _fetch_data_for_intent("account_status", "overview of all accounts", adapter)

    assert [a["name"] for a in result["accounts"]] == [
        "First Account", "Credit Account", "Last Account",
    ]
    assert result["accounts"][1] == {
        "name": "Credit Account", "health_status": "unknown", "health_score": None,
    }
    assert result["accounts"][0]["health_status"] != "unknown"
    assert result["accounts"][2]["health_status"] != "unknown"
    summary = summarize_accounts_overview(result["accounts"])
    assert "Accounts overview (3 total)" in summary
    assert "Unknown / unscored: 1" in summary


@pytest.mark.asyncio
@pytest.mark.parametrize("balance", [None, "not a number", float("nan"), float("inf"), True])
async def test_unobserved_or_invalid_balance_is_not_scored_as_zero(balance):
    adapter = SimpleNamespace(
        get_all_customers=AsyncMock(return_value=[
            SimpleNamespace(qb_id="bad", name="Needs Review", balance=balance),
            SimpleNamespace(qb_id="good", name="Still Scored", balance=0),
        ]),
        get_orders_last_n_days=AsyncMock(return_value=[]),
    )

    result = await _fetch_data_for_intent("account_status", "all account statuses", adapter)
    assert len(result["accounts"]) == 2
    assert result["accounts"][0]["health_status"] == "unknown"
    assert result["accounts"][0]["health_score"] is None
    assert result["accounts"][1]["health_status"] != "unknown"
