"""Shared conventions every advisory insight endpoint relies on."""
import pytest
from fastapi import HTTPException

from app.api.insights import MOUNTED
from app.api.insights._common import advisory_response, read_source, row_value


def test_advisory_response_stamps_analysis_only():
    result = advisory_response({"score": 7}, source="connected_data")
    assert result["score"] == 7
    assert result["status"] == "analysis_only"
    assert result["requires_human_review"] is True
    assert result["actions_executed"] is False
    assert result["source"] == "connected_data"
    assert result["generated_at"]


def test_advisory_response_rejects_unknown_source():
    with pytest.raises(ValueError):
        advisory_response({}, source="guess")


def test_advisory_response_refuses_to_overwrite_result_fields():
    with pytest.raises(ValueError):
        advisory_response({"status": "suggested_change"}, source="user_supplied")
    # An engine's own matching stamp is fine.
    same = advisory_response({"status": "analysis_only", "requires_human_review": True}, source="user_supplied")
    assert same["status"] == "analysis_only"


@pytest.mark.asyncio
async def test_source_failure_is_an_outage_not_an_empty_result():
    async def broken():
        raise ConnectionError("provider down")

    with pytest.raises(HTTPException) as err:
        await read_source(broken(), source_name="QuickBooks")
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_source_success_passes_through():
    async def ok():
        return [1, 2]

    assert await read_source(ok(), source_name="QuickBooks") == [1, 2]


def test_row_value_reads_dicts_and_objects():
    class Row:
        balance = 5

    assert row_value({"balance": 3}, "balance") == 3
    assert row_value(Row(), "balance") == 5
    assert row_value({}, "balance", "x") == "x"


def test_every_insight_module_is_mounted_under_the_paid_api_prefix():
    import importlib

    from fastapi.routing import iter_route_contexts

    from app.main import app

    # Included routers stay nested in FastAPI 0.143; walk the full tree.
    paths = {getattr(ctx, "path", "") for ctx in iter_route_contexts(app.routes)}
    for name in MOUNTED:
        prefix = "/api/insights" + importlib.import_module(f"app.api.insights.{name}").PREFIX
        assert any(p == prefix or p.startswith(prefix + "/") for p in paths), name
