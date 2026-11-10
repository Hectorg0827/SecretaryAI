"""HTTP surface for Lola BI tools (read-only).

GET  /api/lola/bi/tools          → tool definitions the caller may use
POST /api/lola/bi/tools/{name}   → run one tool with a JSON object of arguments

Order of operations is deliberate: authenticate (WS1 ``get_current_user``) →
resolve server-side scope → only then build the provider adapter, so callers
without BI access never trigger credential decryption or token refresh.
Tenant comes from the verified token only.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_adapter, get_db
from app.auth.rbac import get_current_user
from app.lola_bi.contracts import BIAccessDenied, TenantMismatch
from app.lola_bi.gateway import BusinessDataGateway
from app.lola_bi.scope import AssignmentScopeProvider, ScopeUnavailable
from app.lola_bi.tools import TOOLS_BY_NAME, LolaBIService

log = logging.getLogger(__name__)
router = APIRouter()


def get_scope_provider(db=Depends(get_db)):
    """Workstream 1's policy-backed resolver: role, hierarchy, territories and
    delegations re-read from the database on every request (never the JWT role).
    ``AssignmentScopeProvider`` remains available for isolated WS2 tests."""
    from app.lola.integrations import LolaBIScopeProvider
    return LolaBIScopeProvider(db)


async def _scope(user: dict, provider) -> Any:
    try:
        return await provider.resolve(user)
    except BIAccessDenied as exc:
        raise HTTPException(status_code=403, detail="No business-intelligence access for this role") from exc
    except ScopeUnavailable as exc:
        raise HTTPException(status_code=503, detail="Access assignments are unavailable") from exc


def _service(user: dict, scope, db) -> LolaBIService:
    adapter = get_adapter(user=user, db=db)
    try:
        gateway = BusinessDataGateway(adapter, scope, db=db)
    except TenantMismatch as exc:
        log.error("Lola BI connector tenant mismatch")
        raise HTTPException(status_code=403, detail="Connector scope mismatch") from exc
    return LolaBIService(gateway, timezone=user.get("timezone"))


@router.get("/tools")
async def list_tools(user: dict = Depends(get_current_user), provider=Depends(get_scope_provider)):
    scope = await _scope(user, provider)
    from app.lola_bi.tools import DEPARTMENTS, TOOLS
    allowed = [t for t in TOOLS if scope.has(*t.capabilities)]
    return {"contract_version": "lola.bi.v1", "scope": scope.describe(), "departments": DEPARTMENTS,
            "tools": [t.definition() | {"department": t.department} for t in allowed]}


@router.post("/tools/{tool_name}")
async def run_tool(
    tool_name: str,
    arguments: dict = Body(default_factory=dict),
    user: dict = Depends(get_current_user),
    provider=Depends(get_scope_provider),
    db=Depends(get_db),
):
    if tool_name not in TOOLS_BY_NAME:
        raise HTTPException(status_code=404, detail="Unknown tool")
    scope = await _scope(user, provider)
    if not scope.has(*TOOLS_BY_NAME[tool_name].capabilities):
        raise HTTPException(status_code=403, detail="This request is outside your permitted business data")
    result = await _service(user, scope, db).invoke(tool_name, arguments)
    if result["status"] == "invalid_request":
        raise HTTPException(status_code=422, detail=result["warnings"])
    if result["status"] == "denied":
        raise HTTPException(status_code=403, detail="This request is outside your permitted business data")
    # unavailable is a valid, truthful answer (HTTP 200 with status=unavailable),
    # so the UI can show which source is missing instead of a generic error.
    return result
