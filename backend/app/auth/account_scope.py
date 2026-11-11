"""
Which customer accounts a user may see.

Owners, managers and back office see every account in their company. A sales
rep sees only the accounts assigned to them (table `account_assignments`).
This fails CLOSED: if the assignments cannot be read, or an account has no
rep, a sales rep does not see it.

Every place that shows customer data to a sales rep — the accounts list, an
account's detail page, and chat — goes through `visible_customer_ids`.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

SEES_ALL_ACCOUNTS = {"owner", "manager", "back_office"}


def visible_customer_ids(db, user: dict) -> Optional[set[str]]:
    """
    None  → this user may see every account in their company.
    set() → the only QuickBooks customer IDs this user may see (may be empty).
    """
    role = user.get("role", "viewer")
    if role in SEES_ALL_ACCOUNTS:
        return None
    if role != "sales_rep":
        return set()  # viewers and unknown roles: no accounts
    try:
        result = (
            db.table("account_assignments").select("qb_customer_id")
            .eq("company_id", user["company_id"]).eq("user_id", user["sub"]).execute()
        )
        rows = result.data
        if not isinstance(rows, list):
            return set()
        return {str(r["qb_customer_id"]) for r in rows if r.get("qb_customer_id")}
    except Exception as exc:
        log.error("Could not load account assignments (showing none): %s", type(exc).__name__)
        return set()


def can_see_customer(allowed: Optional[set[str]], customer_id: str) -> bool:
    return allowed is None or str(customer_id) in allowed
