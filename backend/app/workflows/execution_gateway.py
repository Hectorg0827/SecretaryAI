"""Internal PostgreSQL transport for SecretaryAI's V11 execution ledger.

Not used by the live approval endpoint. Requires a *separate*, server-only
database credential for a role explicitly allowed to execute only the
secretary_internal functions. Never use a browser Supabase key.
"""
import json
import os
from typing import Any

import asyncpg


class ExecutionDatabaseUnavailable(RuntimeError):
    """Privileged execution transport is unavailable; stop before delivery."""


class PostgresDurableGateway:
    """One short transaction per state transition; no direct table mutation."""

    _ENQUEUE = (
        "SELECT secretary_internal.enqueue_agent_execution($1::text::uuid,$2::text::uuid,$3::text)"
    )
    _CLAIM = (
        "SELECT secretary_internal.claim_agent_execution($1::text::uuid,$2::text::uuid,$3::text)"
    )
    _FINISH = (
        "SELECT secretary_internal.finish_agent_execution("
        "$1::text::uuid,$2::text::uuid,$3::text::uuid,$4::text)"
    )

    _APPROVE = (
        "SELECT secretary_internal.approve_and_queue_email("
        "$1::text::uuid,$2::text::uuid,$3::text::uuid,"
        "$4::text::jsonb,$5::text,$6::text::jsonb)"
    )

    _CLAIM_EMAIL = (
        "SELECT secretary_internal.claim_approved_email("
        "$1::text::uuid,$2::text::uuid,$3::text)"
    )

    _CLAIM_STAGING_EMAIL = (
        "SELECT secretary_internal.claim_staging_email("
        "$1::text::uuid,$2::text::uuid,$3::text,$4::text)"
    )

    def __init__(self, dsn: str, *, required_role: str = "secretary_executor"):
        if not dsn or not required_role:
            raise ExecutionDatabaseUnavailable("Execution database is not configured")
        self._dsn = dsn
        self._required_role = required_role

    @classmethod
    def from_environment(cls) -> "PostgresDurableGateway":
        dsn = os.getenv("SECRETARYAI_EXECUTION_DATABASE_URL", "")
        if not dsn:
            raise ExecutionDatabaseUnavailable(
                "Dedicated execution database credential is required"
            )
        return cls(dsn)

    async def _call(self, sql: str, *args: str) -> dict[str, Any]:
        conn = None
        try:
            conn = await asyncpg.connect(self._dsn, timeout=5, command_timeout=5)
            # Fail closed if someone accidentally configures the broad app DB
            # account or an unauthenticated/user-facing database credential.
            role = await conn.fetchval("SELECT current_user")
            if role != self._required_role:
                raise ExecutionDatabaseUnavailable(
                    "Execution database role is not the restricted worker role"
                )
            result = await conn.fetchval(sql, *args)
            if isinstance(result, str):
                result = json.loads(result)
            if not isinstance(result, dict):
                raise ExecutionDatabaseUnavailable("Invalid execution RPC response")
            return result
        except ExecutionDatabaseUnavailable:
            raise
        except Exception as exc:
            # Do not include exception text; it may contain DSN/user information.
            raise ExecutionDatabaseUnavailable("Execution database request failed") from exc
        finally:
            if conn is not None:
                await conn.close()

    async def approve_and_enqueue(
        self, company_id: str, draft_id: str, actor_id: str,
        expected_content: dict, idempotency_key: str,
        edited_content: dict | None = None,
    ) -> dict[str, Any]:
        """Atomically approve and queue; this does not execute the action."""
        data = await self._call(
            self._APPROVE, company_id, draft_id, actor_id,
            json.dumps(expected_content), idempotency_key,
            json.dumps(edited_content) if edited_content is not None else None,
        )
        if not data.get("execution_id") or type(data.get("queued")) is not bool:
            raise ExecutionDatabaseUnavailable("Invalid durable approval response")
        if data["queued"] is False and data.get("already_processed") is not True:
            raise ExecutionDatabaseUnavailable(
                "Durable approval did not enqueue or establish prior processing"
            )
        return data

    async def enqueue(self, company_id: str, draft_id: str, key: str) -> str:
        data = await self._call(self._ENQUEUE, company_id, draft_id, key)
        execution_id = data.get("execution_id")
        if not execution_id:
            raise ExecutionDatabaseUnavailable("Enqueue result lacks execution identity")
        return str(execution_id)

    async def claim(self, company_id: str, execution_id: str,
                    worker_id: str) -> str | None:
        data = await self._call(self._CLAIM, company_id, execution_id, worker_id)
        if data.get("claimed") is False:
            return None
        token = data.get("claim_token")
        if data.get("claimed") is not True or not token:
            raise ExecutionDatabaseUnavailable("Claim returned invalid worker token")
        return str(token)

    async def claim_email(
        self, company_id: str, execution_id: str, worker_id: str,
    ) -> dict[str, Any]:
        """Claim an immutable email snapshot, never read a mutable draft."""
        data = await self._call(
            self._CLAIM_EMAIL, company_id, execution_id, worker_id,
        )
        if data.get("claimed") is False:
            return {"claimed": False}
        fields = ("claim_token", "recipient", "subject", "body", "payload_sha256")
        if data.get("claimed") is not True or any(
            data.get(key) is None for key in fields
        ):
            raise ExecutionDatabaseUnavailable("Incomplete approved email claim")
        return data

    async def claim_staging_email(
        self, company_id: str, execution_id: str,
        worker_id: str, allowed_recipient: str,
    ) -> dict[str, Any]:
        """DB enforces recipient allowlist before acquiring the claim."""
        data = await self._call(
            self._CLAIM_STAGING_EMAIL,
            company_id, execution_id, worker_id, allowed_recipient,
        )
        if data.get("claimed") is False:
            return {"claimed": False}
        fields = ("claim_token", "recipient", "subject", "body")
        if data.get("claimed") is not True or any(
            data.get(name) is None for name in fields
        ):
            raise ExecutionDatabaseUnavailable("Invalid staging email claim")
        return data

    async def finish(self, company_id: str, execution_id: str,
                     claim_token: str, outcome: str) -> bool:
        data = await self._call(
            self._FINISH, company_id, execution_id, claim_token, outcome
        )
        if type(data.get("finalized")) is not bool:
            raise ExecutionDatabaseUnavailable("Invalid finalization response")
        return data["finalized"]
