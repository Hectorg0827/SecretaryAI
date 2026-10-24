"""V13 end-to-end execution gate against disposable CI PostgreSQL.

NO external email, payment, stock, API or production service is contacted.
Must only run against the disposable localhost 'sai' database in GitHub CI.
"""
import asyncio
import os
import uuid

import asyncpg

from app.workflows.approved_execution import (
    ExecutionReconciliationRequired,
    run_approved_external_action,
)
from app.workflows.execution_gateway import PostgresDurableGateway


async def main() -> None:
    if (os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
            or os.environ.get("PGHOST") not in ("localhost", "127.0.0.1")
            or os.environ.get("PGDATABASE") != "sai"):
        raise RuntimeError("Refusing non-disposable execution test database")

    admin = await asyncpg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        database=os.environ["PGDATABASE"],
    )
    company_a = None
    company_b = None
    try:
        company_a = await admin.fetchval(
            "INSERT INTO companies(name) VALUES($1) RETURNING id",
            "V13 Disposable Execution Tenant A"
        )
        company_b = await admin.fetchval(
            "INSERT INTO companies(name) VALUES($1) RETURNING id",
            "V13 Disposable Execution Tenant B"
        )
        draft_a = await admin.fetchval(
            "INSERT INTO drafts(company_id,created_by,action_type,content,status) "
            "VALUES($1,'ai','email_reply','{}'::jsonb,'approved') RETURNING id",
            company_a,
        )
        draft_b = await admin.fetchval(
            "INSERT INTO drafts(company_id,created_by,action_type,content,status) "
            "VALUES($1,'ai','email_reply','{}'::jsonb,'approved') RETURNING id",
            company_b,
        )
        gateway = PostgresDurableGateway.from_environment()
        effects = 0

        async def synthetic_action() -> None:
            nonlocal effects
            effects += 1

        first = await run_approved_external_action(
            gateway=gateway, company_id=str(company_a),
            draft_id=str(draft_a), idempotency_key="v13-idem-a",
            worker_id="v13-worker-one", external_action=synthetic_action
        )
        second = await run_approved_external_action(
            gateway=gateway, company_id=str(company_a),
            draft_id=str(draft_a), idempotency_key="v13-idem-a",
            worker_id="v13-worker-two", external_action=synthetic_action
        )
        assert first.state == "completed" and first.claimed
        assert second.state == "not_claimed" and not second.claimed
        assert effects == 1, "duplicate synthetic external effect executed"

        async def uncertain_action() -> None:
            nonlocal effects
            effects += 1
            raise TimeoutError("synthetic provider acknowledgement lost")

        try:
            await run_approved_external_action(
                gateway=gateway, company_id=str(company_b),
                draft_id=str(draft_b), idempotency_key="v13-idem-b",
                worker_id="v13-worker-three",
                external_action=uncertain_action,
            )
        except ExecutionReconciliationRequired:
            pass
        else:
            raise AssertionError("uncertain action did not require reconciliation")

        replay = await run_approved_external_action(
            gateway=gateway, company_id=str(company_b),
            draft_id=str(draft_b), idempotency_key="v13-idem-b",
            worker_id="v13-worker-four", external_action=synthetic_action,
        )
        assert replay.state == "not_claimed"
        assert effects == 2, "uncertain action was run again"

        states = await admin.fetch(
            "SELECT company_id,state,attempt FROM agent_executions "
            "WHERE company_id = ANY($1::uuid[])",
            [company_a, company_b],
        )
        by_tenant = {row["company_id"]: row for row in states}
        assert len(states) == 2
        assert by_tenant[company_a]["state"] == "completed"
        assert by_tenant[company_b]["state"] == "needs_reconciliation"
        assert all(row["attempt"] == 1 for row in states)
        print("V13 disposable PostgreSQL end-to-end execution acceptance passed")
    finally:
        # The guard above confines cleanup to synthetic records in CI only.
        if company_a:
            await admin.execute("DELETE FROM companies WHERE id=$1", company_a)
        if company_b:
            await admin.execute("DELETE FROM companies WHERE id=$1", company_b)
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
