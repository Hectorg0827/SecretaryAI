"""
SecretaryAI · Feature Factory — Scheduled Runs (Celery)
=======================================================
Runs OBSERVE and ACTIVE features on their schedule, using your existing Celery
worker + Redis (queues: celery, high_priority — see docker-compose.yml).

Durability: state lives in Postgres (ff_features / ff_feature_runs), never in
memory. If a worker dies mid-run, the run row is left and the circuit breaker /
next tick recovers. Restarting workers or scaling horizontally is safe.

Wiring (add to your celery beat schedule):
    celery_app.conf.beat_schedule["feature-factory-tick"] = {
        "task": "tasks.feature_runs.tick_scheduled_features",
        "schedule": 60.0,   # every minute; the task itself decides what's due
    }

NOTE: The task body shows the structure. It needs the same DB executor + tenant
gateways the API uses. Build them from your worker context (see README
"Running scheduled features"). Marked CONFIRM where host wiring is required.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

# CONFIRM: import your configured Celery app instance.
# from celery_app import celery_app
try:
    from celery_app import celery_app  # type: ignore
except Exception:  # keeps this file importable in isolation/tests
    celery_app = None


def _run_async(coro):
    """Run an async coroutine from a sync Celery task safely."""
    return asyncio.run(coro)


async def _run_due_features() -> dict:
    """
    Find features that are due and run them. Pseudocode-light but real shape:
      1. query ff_features where status in ('observe','active') and schedule due
      2. for each, build a tenant-scoped service and call run_feature(...)
    Implemented with your worker's DB/gateway factories.
    """
    # CONFIRM: build these from your worker context.
    # from app.worker_context import make_db, make_gateways
    # db = await make_db()
    #
    # rows = await db.fetch(
    #     """
    #     SELECT id, tenant_id FROM ff_features
    #      WHERE status IN ('observe','active')
    #        AND trigger_kind = 'schedule'
    #        AND <your-cron-due-check>
    #     """
    # )
    # ran = 0
    # for r in rows:
    #     tenant_id = r["tenant_id"]; feature_id = r["id"]
    #     data, effects = make_gateways(tenant_id)
    #     repo = FeatureRepository(db, tenant_id)
    #     audit = AuditLogger(db, tenant_id)
    #     svc = FeatureFactoryService(repo, audit, data, effects, tenant_id)
    #     try:
    #         await svc.run_feature(feature_id=feature_id, trigger="schedule", user_id=None)
    #         ran += 1
    #     except Exception:
    #         continue  # failure is recorded + circuit-broken inside run_feature
    # return {"ran": ran}
    return {"ran": 0, "note": "wire make_db/make_gateways per README"}


if celery_app is not None:

    @celery_app.task(name="tasks.feature_runs.tick_scheduled_features", queue="celery")
    def tick_scheduled_features() -> dict:
        """Beat calls this every minute; it runs whatever is due."""
        return _run_async(_run_due_features())

    @celery_app.task(name="tasks.feature_runs.run_one_feature", queue="high_priority")
    def run_one_feature(tenant_id: str, feature_id: str) -> dict:
        """On-demand single run, e.g. triggered from the UI for an active feature."""
        async def _one():
            # Same assembly as above for a single (tenant_id, feature_id).
            return {"ran": 1, "tenant": tenant_id, "feature": feature_id,
                    "note": "wire service assembly per README"}
        return _run_async(_one())
