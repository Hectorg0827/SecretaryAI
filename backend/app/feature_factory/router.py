"""
SecretaryAI · Feature Factory — FastAPI Router
==============================================
HTTP surface for the web app. Mount it in app/main.py:

    from app.feature_factory.router import router as feature_factory_router
    app.include_router(feature_factory_router)

⚠️ THREE DEPENDENCY SEAMS (search "SEAM:"). Wire these to your EXISTING auth
and database so the module uses your real users/tenants. They are the only
integration points; everything else is self-contained.

Layer 2 (prompt-injection defense) is enforced structurally here: the ONLY way
to generate a feature is an authenticated human calling POST /features. No
ingested email/file/data path can reach this code.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from .audit import AuditLogger
from .contracts import (
    ApprovalDecision,
    CreateFeatureRequest,
)
from .repository import DBExecutor, FeatureRepository
from .service import FeatureFactoryService, PolicyError

router = APIRouter(prefix="/api/feature-factory", tags=["feature-factory"])


# ===========================================================================
# SEAMS — replace these three with your real dependencies.
# ===========================================================================

# SEAM 1: return your DB executor (asyncpg pool / SQLAlchemy session adapter).
async def get_db() -> DBExecutor:  # pragma: no cover - wired by host app
    raise NotImplementedError("Wire get_db() to your backend's database executor.")

# SEAM 2: return the authenticated user's id from your existing auth.
async def get_current_user_id() -> UUID:  # pragma: no cover
    raise NotImplementedError("Wire get_current_user_id() to your auth (JWT/session).")

# SEAM 3: return the tenant id for the current request from your auth/context.
async def get_tenant_id() -> UUID:  # pragma: no cover
    raise NotImplementedError("Wire get_tenant_id() to your tenant resolution.")

# SEAM 4 (business data): provide your gateways. These read/write your real
# invoices/customers/etc. See engine.py for the protocols and README for stubs.
from .engine import BusinessDataGateway, EffectExecutor  # noqa: E402

async def get_data_gateway(tenant_id: UUID = Depends(get_tenant_id)) -> BusinessDataGateway:  # pragma: no cover
    raise NotImplementedError("Provide a BusinessDataGateway bound to your business tables.")

async def get_effect_executor(tenant_id: UUID = Depends(get_tenant_id)) -> EffectExecutor:  # pragma: no cover
    raise NotImplementedError("Provide an EffectExecutor that performs flag/notify/task effects.")


# ---------------------------------------------------------------------------
# Service assembly (one per request, locked to the caller's tenant).
# ---------------------------------------------------------------------------
async def get_service(
    db: DBExecutor = Depends(get_db),
    tenant_id: UUID = Depends(get_tenant_id),
    data: BusinessDataGateway = Depends(get_data_gateway),
    effects: EffectExecutor = Depends(get_effect_executor),
) -> FeatureFactoryService:
    repo = FeatureRepository(db, tenant_id)
    audit = AuditLogger(db, tenant_id)
    return FeatureFactoryService(repo, audit, data, effects, tenant_id)


def _guard(exc: PolicyError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/features")
async def create_feature(
    body: CreateFeatureRequest,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Step 1: turn plain English into a proposed (pending) feature."""
    try:
        return await svc.create_feature(request_text=body.request_text, user_id=user_id)
    except PolicyError as e:
        raise _guard(e)
    except ValueError as e:
        # interpreter could not produce valid JSON — surface gracefully.
        raise HTTPException(status_code=422, detail=f"Could not understand the request: {e}")


@router.post("/features/{feature_id}/approve-capabilities")
async def approve_capabilities(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Step 2: human approves the plain-English capability list."""
    try:
        await svc.approve_capabilities(feature_id=feature_id, user_id=user_id)
        return {"ok": True}
    except PolicyError as e:
        raise _guard(e)


@router.post("/features/{feature_id}/reject")
async def reject_feature(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        await svc.reject_feature(feature_id=feature_id, user_id=user_id)
        return {"ok": True}
    except PolicyError as e:
        raise _guard(e)


@router.post("/features/{feature_id}/dry-run")
async def dry_run(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Step 3: safe preview — 'here's what I would do'."""
    try:
        return await svc.dry_run(feature_id=feature_id, user_id=user_id)
    except PolicyError as e:
        raise _guard(e)


@router.post("/features/{feature_id}/promote-observe")
async def promote_observe(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        await svc.promote_to_observe(feature_id=feature_id, user_id=user_id)
        return {"ok": True}
    except PolicyError as e:
        raise _guard(e)


@router.post("/features/{feature_id}/promote-active")
async def promote_active(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Step 4: graduate to acting for real (gated by progressive trust)."""
    try:
        await svc.promote_to_active(feature_id=feature_id, user_id=user_id)
        return {"ok": True}
    except PolicyError as e:
        raise _guard(e)


@router.post("/features/{feature_id}/run")
async def run_now(
    feature_id: UUID,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    try:
        return await svc.run_feature(feature_id=feature_id, trigger="manual", user_id=user_id)
    except PolicyError as e:
        raise _guard(e)


@router.post("/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: UUID,
    body: ApprovalDecision,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Step 6: human signs off (or rejects) a commit/destructive action."""
    try:
        return await svc.decide_approval(approval_id=approval_id, approve=body.approve, user_id=user_id)
    except PolicyError as e:
        raise _guard(e)


@router.post("/kill-switch")
async def set_kill_switch(
    enabled: bool,
    svc: FeatureFactoryService = Depends(get_service),
    user_id: UUID = Depends(get_current_user_id),
):
    """Admin master switch for the whole tenant (Layer 7)."""
    await svc.set_kill_switch(enabled=enabled, user_id=user_id)
    return {"ok": True, "enabled": enabled}


# ---- read endpoints for the UI -------------------------------------------
@router.get("/features")
async def list_features(
    db: DBExecutor = Depends(get_db),
    tenant_id: UUID = Depends(get_tenant_id),
):
    repo = FeatureRepository(db, tenant_id)
    return await repo.list_features()


@router.get("/features/{feature_id}/runs")
async def list_runs(
    feature_id: UUID,
    db: DBExecutor = Depends(get_db),
    tenant_id: UUID = Depends(get_tenant_id),
):
    repo = FeatureRepository(db, tenant_id)
    return await repo.list_runs(feature_id)


@router.get("/approvals")
async def list_pending_approvals(
    db: DBExecutor = Depends(get_db),
    tenant_id: UUID = Depends(get_tenant_id),
):
    repo = FeatureRepository(db, tenant_id)
    return await repo.list_pending_approvals()
