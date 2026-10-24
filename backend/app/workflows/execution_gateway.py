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

    async def finish(self, company_id: str, execution_id: str,
                     claim_token: str, outcome: str) -> bool:
        data = await self._call(
            self._FINISH, company_id, execution_id, claim_token, outcome
        )
        if type(data.get("finalized")) is not bool:
            raise ExecutionDatabaseUnavailable("Invalid finalization response")
        return data["finalized"]
