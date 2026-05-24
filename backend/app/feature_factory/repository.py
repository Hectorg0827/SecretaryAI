"""
SecretaryAI · Feature Factory — Repository (Layer 4: tenant isolation)
======================================================================
All database access for the Feature Factory goes through here. The key safety
property: tenant_id is injected by THIS layer on every query. A generated
feature, the engine, or the AI can never construct a query that reaches another
tenant's data, because they never get to write the WHERE clause.

PORTABILITY SEAM
  This repository talks to the database through a tiny `DBExecutor` protocol
  (fetch / fetchrow / execute). Wire it to whatever your backend already uses:
    * asyncpg pool          -> trivial adapter
    * SQLAlchemy AsyncSession-> trivial adapter
    * supabase-py            -> trivial adapter
  We do NOT assume a specific client, so this won't conflict with your stack.
  See README.md "Wiring the database" for 10-line adapters.
"""
from __future__ import annotations

import json
from typing import Any, Optional, Protocol
from uuid import UUID

from .contracts import Capability, FeatureSpec, FeatureStatus


class DBExecutor(Protocol):
    """Minimal async DB interface this module needs. Adapt to your client."""
    async def fetch(self, query: str, *args: Any) -> list[dict]: ...
    async def fetchrow(self, query: str, *args: Any) -> Optional[dict]: ...
    async def execute(self, query: str, *args: Any) -> Any: ...


class FeatureRepository:
    """
    Constructed per-request WITH a tenant_id. Once constructed, it is locked to
    that tenant. There is no method that accepts a different tenant_id.
    """

    def __init__(self, db: DBExecutor, tenant_id: UUID):
        self._db = db
        self._tenant_id = tenant_id

    # -- tenant settings / kill switch -------------------------------------
    async def is_factory_enabled(self) -> bool:
        row = await self._db.fetchrow(
            "SELECT feature_factory_enabled FROM ff_tenant_settings WHERE tenant_id = $1",
            self._tenant_id,
        )
        # Default ON if no settings row yet; absence of a kill switch != killed.
        return True if row is None else bool(row["feature_factory_enabled"])

    async def feature_count(self) -> int:
        row = await self._db.fetchrow(
            "SELECT count(*) AS n FROM ff_features WHERE tenant_id = $1",
            self._tenant_id,
        )
        return int(row["n"]) if row else 0

    # -- features ----------------------------------------------------------
    async def create_feature(
        self,
        *,
        created_by: UUID,
        name: str,
        description: str,
        request_text: str,
        tier: int,
        spec: FeatureSpec,
        declared_capabilities: list[Capability],
        trigger_kind: str,
        schedule_cron: Optional[str],
    ) -> UUID:
        row = await self._db.fetchrow(
            """
            INSERT INTO ff_features
              (tenant_id, created_by, name, description, request_text, tier,
               status, spec, declared_capabilities, trigger_kind, schedule_cron)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id
            """,
            self._tenant_id,
            created_by,
            name,
            description,
            request_text,
            tier,
            FeatureStatus.PENDING_CAPABILITY_APPROVAL.value,
            json.dumps(spec.model_dump(mode="json")),
            json.dumps([c.model_dump(mode="json") for c in declared_capabilities]),
            trigger_kind,
            schedule_cron,
        )
        return row["id"]

    async def get_feature(self, feature_id: UUID) -> Optional[dict]:
        # tenant_id in the WHERE clause is non-negotiable and always present.
        return await self._db.fetchrow(
            "SELECT * FROM ff_features WHERE id = $1 AND tenant_id = $2",
            feature_id,
            self._tenant_id,
        )

    async def list_features(self) -> list[dict]:
        return await self._db.fetch(
            "SELECT * FROM ff_features WHERE tenant_id = $1 ORDER BY created_at DESC",
            self._tenant_id,
        )

    async def set_status(self, feature_id: UUID, status: FeatureStatus) -> None:
        await self._db.execute(
            "UPDATE ff_features SET status = $1 WHERE id = $2 AND tenant_id = $3",
            status.value,
            feature_id,
            self._tenant_id,
        )

    async def record_observe_success(self, feature_id: UUID) -> dict:
        return await self._db.fetchrow(
            """
            UPDATE ff_features
               SET successful_observe_runs = successful_observe_runs + 1,
                   consecutive_failures = 0
             WHERE id = $1 AND tenant_id = $2
            RETURNING successful_observe_runs, observe_runs_required
            """,
            feature_id,
            self._tenant_id,
        )

    async def record_failure(self, feature_id: UUID) -> dict:
        """Increment failure counter; caller checks against threshold (Layer 7)."""
        return await self._db.fetchrow(
            """
            UPDATE ff_features
               SET consecutive_failures = consecutive_failures + 1
             WHERE id = $1 AND tenant_id = $2
            RETURNING consecutive_failures, failure_threshold
            """,
            feature_id,
            self._tenant_id,
        )

    # -- capability grants -------------------------------------------------
    async def save_grants(self, feature_id: UUID, grants: list[Capability], approved_by: UUID) -> None:
        for g in grants:
            await self._db.execute(
                """
                INSERT INTO ff_capability_grants
                  (feature_id, tenant_id, capability_key, resource, scope, approved_by)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                feature_id, self._tenant_id, g.capability_key, g.resource, g.scope.value, approved_by,
            )

    async def get_grants(self, feature_id: UUID) -> list[dict]:
        return await self._db.fetch(
            "SELECT * FROM ff_capability_grants WHERE feature_id = $1 AND tenant_id = $2",
            feature_id,
            self._tenant_id,
        )

    # -- runs --------------------------------------------------------------
    async def create_run(
        self, *, feature_id: UUID, mode: str, status: str, trigger: str, correlation_id: UUID,
    ) -> UUID:
        row = await self._db.fetchrow(
            """
            INSERT INTO ff_feature_runs
              (feature_id, tenant_id, mode, status, trigger, correlation_id)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id
            """,
            feature_id, self._tenant_id, mode, status, trigger, correlation_id,
        )
        return row["id"]

    async def finish_run(
        self, run_id: UUID, *, status: str, planned: list[dict], executed: list[dict],
        result_summary: Optional[str], error: Optional[str],
    ) -> None:
        await self._db.execute(
            """
            UPDATE ff_feature_runs
               SET status = $1, planned_actions = $2, executed_actions = $3,
                   result_summary = $4, error = $5, finished_at = now()
             WHERE id = $6 AND tenant_id = $7
            """,
            status, json.dumps(planned), json.dumps(executed),
            result_summary, error, run_id, self._tenant_id,
        )

    async def list_runs(self, feature_id: UUID, limit: int = 50) -> list[dict]:
        return await self._db.fetch(
            """
            SELECT * FROM ff_feature_runs
             WHERE feature_id = $1 AND tenant_id = $2
             ORDER BY started_at DESC LIMIT $3
            """,
            feature_id, self._tenant_id, limit,
        )

    # -- approvals ---------------------------------------------------------
    async def create_approval(
        self, *, feature_id: UUID, run_id: UUID, action: dict, action_class: str,
        before_snapshot: Optional[dict],
    ) -> UUID:
        row = await self._db.fetchrow(
            """
            INSERT INTO ff_approvals
              (feature_id, run_id, tenant_id, action, action_class, before_snapshot)
            VALUES ($1,$2,$3,$4,$5,$6)
            RETURNING id
            """,
            feature_id, run_id, self._tenant_id,
            json.dumps(action), action_class,
            json.dumps(before_snapshot) if before_snapshot else None,
        )
        return row["id"]

    async def list_pending_approvals(self) -> list[dict]:
        return await self._db.fetch(
            "SELECT * FROM ff_approvals WHERE tenant_id = $1 AND status = 'pending' ORDER BY requested_at",
            self._tenant_id,
        )

    async def decide_approval(self, approval_id: UUID, *, approved: bool, decided_by: UUID) -> Optional[dict]:
        return await self._db.fetchrow(
            """
            UPDATE ff_approvals
               SET status = $1, decided_by = $2, decided_at = now()
             WHERE id = $3 AND tenant_id = $4 AND status = 'pending'
            RETURNING *
            """,
            "approved" if approved else "rejected",
            decided_by, approval_id, self._tenant_id,
        )
