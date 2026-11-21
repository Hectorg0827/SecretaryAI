"""Owner-only, feature-flagged AR cash application staging acceptance route.

The only output is bounded aggregate counts. This endpoint is not accessible
in production and remains disabled in staging until the specific feature flag
is approved. It never executes payment, invoice or ledger mutations.

QuickBooks token refresh may rotate and persist OAuth credentials as part of a
READ request; this is credential housekeeping, not a financial transaction.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import BoundedSemaphore
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    claim_qbo_ar_preview_window,
    get_qbo_ar_preview_adapter,
    get_db,
    verify_qbo_ar_preview_connection,
)
from app.api.finance_workspace import _current_role
from app.auth.rbac import require_permission
from app.config import get_settings

router = APIRouter()
NO_STORE = {"Cache-Control": "private, no-store"}

# Redis hostname resolution occurs before the socket connection deadline.
# Timed-out asyncio.to_thread calls therefore may keep blocking default-pool
# workers. Use a separate, small executor with capacity held until each
# underlying thread genuinely completes (not until the await is canceled).
_AR_REDIS_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="finance-ar-redis",
)
_AR_REDIS_CAPACITY = BoundedSemaphore(2)


def _submit_ar_preview_cooldown(*, company_id: str):
    """Bound in-flight Redis work even if DNS ignores request cancellation."""
    if not _AR_REDIS_CAPACITY.acquire(blocking=False):
        raise RuntimeError("AR preview cooldown capacity unavailable")
    try:
        future = _AR_REDIS_EXECUTOR.submit(
            claim_qbo_ar_preview_window, company_id=company_id,
        )
    except BaseException:
        _AR_REDIS_CAPACITY.release()
        raise
    # Completion callbacks also run if a queued future gets canceled.
    # Do not release capacity merely because asyncio.wait_for timed out.
    future.add_done_callback(lambda _finished: _AR_REDIS_CAPACITY.release())
    return future


# Aggregate source counts must never accept negative, bool or coerced string
# values. Keep None for deliberately unavailable counts in partial evidence.
StrictCount = Annotated[int, Field(ge=0, strict=True)]


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
    invoice_count: StrictCount | None
    linked_invoice_count: StrictCount | None
    variance_invoice_count: StrictCount | None
    partially_paid_invoice_count: StrictCount | None
    unapplied_payment_count: StrictCount | None
    accounting_reconciled: Literal[False]
    financial_write_authorized: Literal[False]


@router.get("/ar-payment-preview", response_model=ARPaymentPreviewResponse)
async def get_ar_payment_preview(
    response: Response,
    user: dict = Depends(require_permission("view_finance_workspace")),
    db=Depends(get_db),
):
    """On-demand owner acceptance, without exporting invoice/payment details."""
    response.headers.update(NO_STORE)

    config = get_settings()
    if (config.secretaryai_deployment_env.strip().lower() != "staging"
            or config.secretaryai_ar_payment_preview_enabled is not True):
        raise HTTPException(status_code=404, detail="Not found", headers=NO_STORE)

    # A valid JWT alone does not prove the current DB role is still Owner.
    # Authorization must happen before constructing any provider adapters.
    try:
        current_role = await asyncio.to_thread(_current_role, db, user)
    except HTTPException as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=("Finance preview authorization unavailable"
                    if exc.status_code == 503 else "Finance preview not authorized"),
            headers=NO_STORE,
        ) from None
    if current_role != "owner":
        raise HTTPException(
            status_code=403, detail="Finance preview not authorized",
            headers=NO_STORE,
        )

    # An authorized staging preview may trigger many provider GETs due to
    # pagination and stability checks. Enforce a cross-replica tenant cooldown
    # before constructing QBO, while failing closed if Redis is unavailable.
    try:
        allowed = await asyncio.wait_for(
            asyncio.wrap_future(
                _submit_ar_preview_cooldown(company_id=user["company_id"]),
            ),
            timeout=3,
        )
    except Exception:
        raise HTTPException(
            status_code=503, detail="AR preview cooldown unavailable",
            headers=NO_STORE,
        ) from None
    if not allowed:
        raise HTTPException(
            status_code=429, detail="AR preview cooldown active",
            headers={**NO_STORE, "Retry-After": "60"},
        )

    as_of = datetime.now(timezone.utc).date()
    try:
        # Built from the already authenticated tenant's company row, not a
        # client-supplied realm ID or company ID.
        # Bound the entire staging acceptance read, including blocking
        # company lookups, both provider passes and post-read role/realm
        # verification. A timeout must never produce partial counts.
        async with asyncio.timeout(30):
            adapter = await asyncio.to_thread(
                get_qbo_ar_preview_adapter, user=user, db=db,
            )
            result = await adapter.get_qbo_ar_payment_match_preview_read_only(
                company_id=user["company_id"], as_of=as_of,
            )
            await asyncio.to_thread(
                verify_qbo_ar_preview_connection,
                db=db, company_id=user["company_id"],
                expected_realm=adapter._config["qbo_realm_id"],
            )
            if await asyncio.to_thread(_current_role, db, user) != "owner":
                raise PermissionError("AR preview owner authorization changed")
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
            status_code=503, detail="AR payment evidence unavailable or unverified",
            headers=NO_STORE,
        ) from None
