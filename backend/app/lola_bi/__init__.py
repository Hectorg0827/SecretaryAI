"""Lola business intelligence (Workstream 2): read-only, permission-scoped tools.

Public interface for Workstream 1:

    from app.lola_bi import (
        AssignmentScopeProvider, BIScopeProvider, BIAccessScope,
        BusinessDataGateway, SnapshotCache, LolaBIService,
    )

See docs/workstreams/02-data-finance/lola_bi_workstream2.md.
"""
from app.lola_bi.contracts import CONTRACT_VERSION, SourceSnapshot, SourceStatus
from app.lola_bi.gateway import BusinessDataGateway, SnapshotCache
from app.lola_bi.scope import AssignmentScopeProvider, BIAccessScope, BIScopeProvider, Cap
from app.lola_bi.tools import DEPARTMENTS, TOOLS, LolaBIService

__all__ = [
    "CONTRACT_VERSION", "SourceSnapshot", "SourceStatus", "BusinessDataGateway", "SnapshotCache",
    "AssignmentScopeProvider", "BIAccessScope", "BIScopeProvider", "Cap", "DEPARTMENTS", "TOOLS",
    "LolaBIService",
]
