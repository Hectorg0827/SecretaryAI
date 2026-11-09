#!/usr/bin/env python3
"""Lola executor against a DISPOSABLE PostgreSQL, as the restricted executor role.

Real SQL (approve_and_queue_*, claim_*, finish_agent_execution) and the real
executor code; only the mail / QuickBooks connector is a local fake, so nothing
leaves this machine. Refuses to run against anything but localhost/sai.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import asyncpg  # noqa: E402

from app.workflows.execution_gateway import PostgresDurableGateway  # noqa: E402
from app.workflows.lola_executor import (  # noqa: E402
    execute_approved_email, execute_approved_purchase_order,
)


class FakeMail:
    def __init__(self, company_id):
        self._config = {"id": company_id}
        self.sent = []

    async def send_email_reply(self, to, subject, body):
        self.sent.append(to)
        return {"status": "sent", "message_id": "ci-msg-1"}


class FakeQBO:
    def __init__(self, company_id):
        self._config = {"id": company_id}
        self.created = []

    def po_execution_target(self):
        return "qbo", True

    async def create_purchase_order_idempotent(self, *, vendor_id, line_items, memo, request_id):
        self.created.append(request_id)
        return SimpleNamespace(qb_id="ci-po-187")


async def main() -> None:
    if (os.environ.get("SECRETARYAI_TEST_DATABASE_ONLY") != "1"
            or os.environ.get("PGHOST") not in ("localhost", "127.0.0.1")
            or os.environ.get("PGDATABASE") != "sai"):
        raise RuntimeError("Refusing non-disposable execution test database")
    os.environ.update({
        "SECRETARYAI_LOLA_EXECUTOR_ENABLED": "1", "SECRETARYAI_EMAIL_DELIVERY_MODE": "live",
        "SECRETARYAI_LIVE_EMAIL_DELIVERY_APPROVED": "1", "SECRETARYAI_PO_EXECUTION_MODE": "sandbox",
    })
    os.environ.pop("SECRETARYAI_DEPLOYMENT_ENV", None)

    admin = await asyncpg.connect(host=os.environ["PGHOST"], port=int(os.environ.get("PGPORT", "5432")),
                                  user=os.environ["PGUSER"], password=os.environ.get("PGPASSWORD"),
                                  database=os.environ["PGDATABASE"])
    try:
        co = await admin.fetchval("INSERT INTO companies(name) VALUES('Lola executor CI') RETURNING id")
        owner = await admin.fetchval(
            "INSERT INTO users(company_id,email,hashed_password,role) VALUES($1,$2,'x','owner') RETURNING id",
            co, f"lola-exec-{co}@example.test")
        email = {"to": "buyer@example.test", "subject": "Following up", "body": "Hello",
                 "_lola": {"receipt_id": "r1"}}
        po = {"vendor_id": "V-7", "total": "1625.00", "_lola": {"receipt_id": "r2"},
              "lines": [{"item_id": "SKU-1", "quantity": 500, "unit_cost": "3.25", "description": "Olive Oil"}]}
        d_email = await admin.fetchval(
            "INSERT INTO drafts(company_id,created_by,action_type,content,status) "
            "VALUES($1,'ai','email_reply',$2::jsonb,'pending') RETURNING id", co, json.dumps(email))
        d_po = await admin.fetchval(
            "INSERT INTO drafts(company_id,created_by,action_type,content,status) "
            "VALUES($1,'ai','purchase_order',$2::jsonb,'pending') RETURNING id", co, json.dumps(po))

        gw = PostgresDurableGateway.from_environment()
        stored_email = json.loads(await admin.fetchval("SELECT content::text FROM drafts WHERE id=$1", d_email))
        stored_po = json.loads(await admin.fetchval("SELECT content::text FROM drafts WHERE id=$1", d_po))
        a1 = await gw.approve_and_enqueue(str(co), str(d_email), str(owner), stored_email, f"approved-draft:{d_email}")
        a2 = await gw.approve_and_enqueue_purchase_order(str(co), str(d_po), str(owner), stored_po,
                                                         f"approved-draft:{d_po}")

        mail, qbo = FakeMail(str(co)), FakeQBO(str(co))
        r1 = await execute_approved_email(gateway=gw, adapter=mail, company_id=str(co),
                                          execution_id=str(a1["execution_id"]), worker_id="ci-1")
        r2 = await execute_approved_purchase_order(gateway=gw, adapter=qbo, company_id=str(co),
                                                   execution_id=str(a2["execution_id"]), worker_id="ci-2")
        assert r1.state == "completed" and r2.state == "completed", (r1, r2)
        # Replays never act twice.
        again1 = await execute_approved_email(gateway=gw, adapter=mail, company_id=str(co),
                                              execution_id=str(a1["execution_id"]), worker_id="ci-3")
        again2 = await execute_approved_purchase_order(gateway=gw, adapter=qbo, company_id=str(co),
                                                       execution_id=str(a2["execution_id"]), worker_id="ci-4")
        assert again1.state == again2.state == "not_claimed"
        assert mail.sent == ["buyer@example.test"] and qbo.created == [f"lola-{a2['execution_id']}"[:50]]

        rows = {r["action_type"]: r for r in await admin.fetch(
            "SELECT action_type,state,external_reference,attempt FROM agent_executions WHERE company_id=$1", co)}
        assert rows["email_reply"]["state"] == "completed"
        assert rows["email_reply"]["external_reference"] == "email:ci-msg-1"
        assert rows["purchase_order"]["state"] == "completed"
        assert rows["purchase_order"]["external_reference"] == "qbo:po:ci-po-187"
        assert all(r["attempt"] == 1 for r in rows.values())
        print("Lola executor disposable PostgreSQL acceptance passed (email + purchase order)")
    finally:
        await admin.close()


if __name__ == "__main__":
    asyncio.run(main())
