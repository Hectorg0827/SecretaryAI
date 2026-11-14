"""Stale-role-safe, tenant-limited authorization for pricing and billbacks.

Pricing staff must not inherit broad finance, purchasing, QuickBooks or
administrative permissions merely to view cost analysis. A token's role is
revalidated against the current, active user row on EACH request.
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

from app.api.deps import get_db
from app.auth.rbac import ROLE_PERMISSIONS, get_current_user

log = logging.getLogger(__name__)


def get_pricing_user(
    user: dict = Depends(get_current_user),
    db=Depends(get_db),
) -> dict:
    company_id = user.get("company_id")
    user_id = user.get("sub")
    role = user.get("role")
    if (not company_id or not user_id
            or "view_pricing" not in ROLE_PERMISSIONS.get(role, set())):
        raise HTTPException(status_code=403, detail="Pricing access denied")
    try:
        rows = (
            db.table("users")
            .select("id,company_id,role,is_active")
            .eq("id", user_id)
            .eq("company_id", company_id)
            .limit(2)
            .execute().data
        )
    except Exception as exc:
        log.warning("Pricing authorization database unavailable")
        raise HTTPException(
            status_code=503, detail="Pricing authorization unavailable",
        ) from exc
    if (not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict)
            or str(rows[0].get("id")) != str(user_id)
            or str(rows[0].get("company_id")) != str(company_id)
            or rows[0].get("role") != role
            or rows[0].get("is_active") is not True):
        raise HTTPException(status_code=403, detail="Pricing access denied")
    return user
