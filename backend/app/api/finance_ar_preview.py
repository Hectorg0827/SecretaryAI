"""Owner-only, feature-flagged AR cash application staging acceptance route.

The only output is bounded aggregate counts. This endpoint is not accessible
in production and remains disabled in staging until the specific feature flag
is approved. It never executes payment, invoice or ledger mutations.

QuickBooks token refresh may rotate and persist OAuth credentials as part of a
READ request; this is credential housekeeping, not a financial transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_adapter, get_db
from app.api.finance_workspace import _current_role
from app.auth.rbac import require_permission
from app.config import get_settings

router = APIRouter()


class ARPaymentPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: Literal["qbo_sandbox"]
    source_quality: Literal["partial"]
    as_of: str
    status: Literal["aligned_not_certified", "variance_detected", "not_assessed"]
    reason: Literal[
        "other_adjustments_or_missing_links_possible",
        "complete_source_coverage_unverified",
    ] | None
    invoice_count: int | None
    linked_invoice_count: int | None
    variance_invoice_count: int | None
    partially_paid_invoice_count: int | None
    unapplied_payment_count: int | None
    accounting_reconciled: Literal[False]
    financial_write_authorized: Literal[False]


@router.get("/ar-payment-preview", response_model=ARPaymentPreviewResponse)
async def get_ar_payment_preview(
    response: Response,
    user: dict = Depends(require_permission("view_finance_workspace")),
    db=Depends(get_db),
):
    """On-demand owner acceptance, without exporting invoice/payment details."""
    response.headers["Cache-Control"] = "private, no-store"

    config = get_settings()
    if (config.secretaryai_deployment_env.strip().lower() != "staging"
            or config.secretaryai_ar_payment_preview_enabled is not True):
        raise HTTPException(status_code=404, detail="Not found")

    # A valid JWT alone does not prove the current DB role is still Owner.
    # Authorization must happen before constructing any provider adapters.
    if _current_role(db, user) != "owner":
        raise HTTPException(status_code=403, detail="Finance preview not authorized")

    as_of = datetime.now(timezone.utc).date()
    try:
        # Built from the already authenticated tenant's company row, not a
        # client-supplied realm ID or company ID.
        adapter = get_adapter(user=user, db=db)
        result = await adapter.get_qbo_ar_payment_match_preview_read_only(
            company_id=user["company_id"], as_of=as_of,
        )
        return ARPaymentPreviewResponse.model_validate({
            **result,
            "source_system": "qbo_sandbox",
            "source_quality": "partial",
            "as_of": as_of.isoformat(),
        })
    except Exception:
        # Never leak raw provider bodies, OAuth failures, source IDs or amounts
        # in responses (or emit the exception body into application logs).
        raise HTTPException(
            status_code=503, detail="AR payment evidence unavailable or unverified"
        ) from None
