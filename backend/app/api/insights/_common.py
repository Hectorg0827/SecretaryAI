"""Shared conventions for advisory insight endpoints.

- Tenant scope comes only from the authenticated user (``user["company_id"]``);
  never accept a company id from the request.
- A failed source read is an outage (HTTP 503), never an empty or zero result.
- Responses are advisory: ``advisory_response`` stamps every payload so the
  UI and audits can tell analysis apart from executed actions.
- Logs use constant messages; never interpolate request or provider values.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Literal, TypeVar

from fastapi import HTTPException

log = logging.getLogger(__name__)

T = TypeVar("T")

# connected_data: computed from the tenant's connectors/database.
# user_supplied: a what-if calculation over values the user entered.
Source = Literal["connected_data", "user_supplied"]


def advisory_response(result: dict[str, Any], *, source: Source) -> dict[str, Any]:
    """Return ``result`` stamped as an analysis-only recommendation."""
    if source not in ("connected_data", "user_supplied"):
        raise ValueError("Unknown advisory source")
    return {
        **result,
        "status": "analysis_only",
        "source": source,
        "requires_human_review": True,
        "actions_executed": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def read_source(call: Awaitable[T], *, source_name: str) -> T:
    """Await a provider/database read; any failure becomes HTTP 503."""
    try:
        return await call
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Insight source read failed")
        raise HTTPException(
            status_code=503, detail=f"{source_name} is unavailable; no analysis was produced",
        ) from exc


def row_value(row: Any, field: str, default: Any = None) -> Any:
    """Read a field from a normalized dict or a connector dataclass."""
    if isinstance(row, dict):
        return row.get(field, default)
    return getattr(row, field, default)
