"""Explicit finance department scopes, distinct from general RBAC roles.

This is a read-only policy. Finance roles are available only to company owners
through the staging mailbox-verified invitation workflow and DB constraint.
"""
from __future__ import annotations

from types import MappingProxyType


_SCOPES = {
    "owner": frozenset({"ar", "ap", "ledger", "pricing"}),
    "manager": frozenset({"ar", "ap", "ledger", "pricing"}),
    "finance_controller": frozenset({"ar", "ap", "ledger"}),
    "ar_specialist": frozenset({"ar"}),
    "ap_specialist": frozenset({"ap"}),
    "pricing_billbacks": frozenset({"pricing"}),
}
FINANCE_SCOPES = MappingProxyType(_SCOPES)


def finance_scopes_for_role(role: str) -> frozenset[str]:
    """Unknown/other roles receive no finance data. Exact-match only."""
    return FINANCE_SCOPES.get(role, frozenset())
