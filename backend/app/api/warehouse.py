"""Role-gated warehouse work-order previews under /api/logistics/warehouse.

No endpoint here reads unverified data as an authoritative stock ledger or
persists, approves, dispatches or communicates a warehouse action.
"""

from fastapi import APIRouter, Depends

from app.auth.rbac import require_permission
from app.intelligence.wholesale_distribution.warehouse.planning import (
    CapacityRequest,
    ClaimPreviewRequest,
    CycleCountRequest,
    PickPlanRequest,
    preview_capacity,
    preview_cycle_count,
    preview_supplier_claim,
    propose_picks,
)

router = APIRouter()


@router.post("/pick-plan")
async def plan_picking(
    request: PickPlanRequest,
    user: dict = Depends(require_permission("view_orders")),
):
    """Propose FEFO bin picks; no stock reservations or order fulfillment."""
    return propose_picks(request, company_id=user["company_id"])


@router.post("/cycle-count")
async def review_cycle_count(
    request: CycleCountRequest,
    user: dict = Depends(require_permission("view_orders")),
):
    """Identify recount candidates; never post inventory adjustments."""
    return preview_cycle_count(request, company_id=user["company_id"])


@router.post("/capacity")
async def review_warehouse_space(
    request: CapacityRequest,
    user: dict = Depends(require_permission("view_inventory")),
):
    """Estimate physical occupancy and draft slotting improvements."""
    return preview_capacity(request, company_id=user["company_id"])


@router.post("/claim-preview")
async def review_supplier_quality_claim(
    request: ClaimPreviewRequest,
    user: dict = Depends(require_permission("view_orders")),
):
    """Draft a shortage/damage claim with no supplier or financial side effect."""
    return preview_supplier_claim(request, company_id=user["company_id"])
