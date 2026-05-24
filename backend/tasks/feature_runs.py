"""
SecretaryAI · Feature Factory — Scheduled Runs (Celery)
=======================================================
Runs OBSERVE and ACTIVE features on schedule. The beat entry is registered in
celery_app.py:

    "feature-factory-tick": {
        "task": "tasks.feature_runs.tick_scheduled_features",
        "schedule": 60.0,   # every minute; the task decides what's actually due
    }

Durability: state lives in Postgres (ff_features / ff_feature_runs), never in
memory. A failed run is recorded and the circuit breaker auto-disables a
feature after `failure_threshold` consecutive failures.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import asyncpg

from app.config import get_settings
from app.feature_factory.audit import AuditLogger
from app.feature_factory.repository import FeatureRepository
from app.feature_factory.service import FeatureFactoryService
from app.feature_factory.wiring import (
    AsyncpgExecutor,
    CompanyDataGateway,
    CompanyEffectExecutor,
)

try:
    from celery_app import celery_app  # type: ignore
except Exception:  # keeps this file importable in isolation/tests
    celery_app = None


log = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    return asyncio.run(coro)


async def _run_feature(pool: asyncpg.Pool, tenant_id: UUID, feature_id: UUID) -> bool:
    """Assemble a tenant-scoped service and execute one feature. Returns True on success."""
    db = AsyncpgExecutor(pool)
    repo = FeatureRepository(db, tenant_id)
    audit = AuditLogger(db, tenant_id)
    data = CompanyDataGateway(pool, tenant_id)
    effects = CompanyEffectExecutor(pool, tenant_id)
    svc = FeatureFactoryService(repo, audit, data, effects, tenant_id)
    try:
        await svc.run_feature(feature_id=feature_id, trigger="schedule", user_id=None)
        return True
    except Exception as exc:
        # The service already recorded a failed run + advanced the circuit
        # breaker. Log here so it surfaces in worker logs too.
        log.warning("feature %s run failed: %s", feature_id, exc)
        return False


async def _run_due_features() -> dict:
    """
    Find features that are due and run them.

    Tier 1 scheduling: we don't yet parse `schedule_cron`; instead we run every
    schedule-triggered feature whose last run was at least 5 minutes ago (or
    that has never run). Observe-mode is read-only and act-mode still gates
    commit/destructive actions behind human approval, so this is safe.
    """
    pool = await asyncpg.create_pool(
        get_settings().database_url, min_size=1, max_size=3
    )
    try:
        rows = await pool.fetch(
            """
            SELECT f.id, f.tenant_id
              FROM ff_features f
              LEFT JOIN LATERAL (
                SELECT max(started_at) AS last_run
                  FROM ff_feature_runs r
                 WHERE r.feature_id = f.id
              ) lr ON true
             WHERE f.status IN ('observe', 'active')
               AND f.trigger_kind = 'schedule'
               AND (lr.last_run IS NULL OR lr.last_run < now() - interval '5 minutes')
            """
        )

        ran = 0
        failed = 0
        for r in rows:
            ok = await _run_feature(pool, r["tenant_id"], r["id"])
            if ok:
                ran += 1
            else:
                failed += 1
        return {"ran": ran, "failed": failed, "considered": len(rows)}
    finally:
        await pool.close()


async def _run_one(tenant_id: UUID, feature_id: UUID) -> dict:
    pool = await asyncpg.create_pool(
        get_settings().database_url, min_size=1, max_size=2
    )
    try:
        ok = await _run_feature(pool, tenant_id, feature_id)
        return {"ran": int(ok), "tenant": str(tenant_id), "feature": str(feature_id)}
    finally:
        await pool.close()


if celery_app is not None:

    @celery_app.task(name="tasks.feature_runs.tick_scheduled_features", queue="celery")
    def tick_scheduled_features() -> dict:
        """Beat calls this every minute; it runs whatever is due."""
        return _run_async(_run_due_features())

    @celery_app.task(name="tasks.feature_runs.run_one_feature", queue="high_priority")
    def run_one_feature(tenant_id: str, feature_id: str) -> dict:
        """On-demand single run, e.g. triggered from the UI for an active feature."""
        return _run_async(_run_one(UUID(tenant_id), UUID(feature_id)))
