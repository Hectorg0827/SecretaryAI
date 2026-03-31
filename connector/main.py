"""
SecretaryAI QB Desktop Connector — main entry point.

Usage:
  python -m connector.main

Environment variables (set in .env or shell):
  SECRETARY_CLOUD_URL      Cloud API base URL
  SECRETARY_INSTALL_SECRET One-time install secret (only needed for first registration)
  SECRETARY_COMPANY_ID     Company UUID
  CONDUCTOR_API_KEY        Conductor API key for QuickBooks Desktop
  CONDUCTOR_END_USER_ID    Conductor end-user ID

The connector runs an infinite loop:
  1. Register with the cloud (once; persists token to connector_state.json)
  2. Send heartbeat every HEARTBEAT_INTERVAL_SECONDS (60s)
  3. Poll for tasks every TASK_POLL_INTERVAL_SECONDS (30s)
  4. Execute tasks and submit results
  5. Report errors with recoverable hints
"""
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import httpx

from connector.config import ConnectorConfig
from connector.executor import TaskExecutor, TaskError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("connector")

# Intervals (seconds)
HEARTBEAT_INTERVAL = 60
TASK_POLL_INTERVAL = 30
RETRY_BACKOFF_BASE = 5   # seconds; doubles on each consecutive failure, max 300
MAX_RETRY_BACKOFF = 300


class Connector:
    def __init__(self, cfg: ConnectorConfig):
        self.cfg = cfg
        self.executor = TaskExecutor(
            conductor_api_key=cfg.conductor_api_key,
            conductor_end_user_id=cfg.conductor_end_user_id,
        )
        self._last_heartbeat = 0.0
        self._last_poll = 0.0
        self._consecutive_errors = 0
        self._poll_interval = TASK_POLL_INTERVAL  # may be updated by heartbeat response

    # ── Public entry ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main loop: register → heartbeat/poll indefinitely."""
        log.info("SecretaryAI Connector v%s starting", self.cfg.version)
        log.info("Company: %s | Connector ID: %s", self.cfg.company_id, self.cfg.connector_id)

        await self._register_with_retry()

        log.info("Connector registered. Starting heartbeat/task loop.")

        while True:
            try:
                await self._tick()
                self._consecutive_errors = 0
            except Exception as exc:
                self._consecutive_errors += 1
                backoff = min(
                    RETRY_BACKOFF_BASE * (2 ** (self._consecutive_errors - 1)),
                    MAX_RETRY_BACKOFF,
                )
                log.error("Connector tick error (attempt %d): %s — retrying in %ds",
                          self._consecutive_errors, exc, backoff)
                await asyncio.sleep(backoff)
                continue

            await asyncio.sleep(5)  # check every 5s; heartbeat/poll run on their own timers

    # ── Registration ──────────────────────────────────────────────────────────

    async def _register_with_retry(self) -> None:
        """Register until successful. Uses exponential backoff."""
        if self.cfg.is_registered:
            log.info("Already registered (token loaded from state file)")
            return

        attempt = 0
        while True:
            attempt += 1
            try:
                await self._register()
                return
            except Exception as exc:
                backoff = min(RETRY_BACKOFF_BASE * (2 ** (attempt - 1)), MAX_RETRY_BACKOFF)
                log.error("Registration attempt %d failed: %s — retrying in %ds", attempt, exc, backoff)
                await asyncio.sleep(backoff)

    async def _register(self) -> None:
        """Call POST /api/connectors/register and persist the token."""
        if not self.cfg.install_secret:
            raise RuntimeError(
                "SECRETARY_INSTALL_SECRET is required for first registration. "
                "Get it from Settings > Connectors in the web app."
            )

        async with _client(self.cfg) as client:
            resp = await client.post(
                "/api/connectors/register",
                json={
                    "company_id": self.cfg.company_id,
                    "connector_type": self.cfg.connector_type,
                    "connector_id": self.cfg.connector_id,
                    "version": self.cfg.version,
                    "install_secret": self.cfg.install_secret,
                    "capabilities": [
                        "customers", "invoices", "inventory", "payments", "vendors",
                    ],
                },
                headers=_user_auth_header(self.cfg),
            )
            resp.raise_for_status()
            data = resp.json()

        self.cfg.save_token(data["connector_token"], data["registration_id"])
        self._poll_interval = data.get("task_poll_interval_seconds", TASK_POLL_INTERVAL)
        log.info("Registered successfully. Registration ID: %s", data["registration_id"])

    # ── Tick ──────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Called every 5s. Runs heartbeat and/or task poll when due."""
        now = time.monotonic()

        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL:
            await self._heartbeat()
            self._last_heartbeat = now

        if now - self._last_poll >= self._poll_interval:
            await self._poll_and_execute()
            self._last_poll = now

    # ── Heartbeat ──────────────────────────────────────────────────────────────

    async def _heartbeat(self) -> None:
        async with _client(self.cfg) as client:
            resp = await client.post(
                "/api/connectors/heartbeat",
                json={
                    "registration_id": self.cfg.registration_id,
                    "status": "ok",
                    "metrics": {
                        "connector_version": self.cfg.version,
                        "uptime_seconds": int(time.time()),
                    },
                },
                headers=_connector_auth_header(self.cfg),
            )
            resp.raise_for_status()
            body = resp.json()
            pending = body.get("pending_task_count", 0)
            if pending:
                log.info("Heartbeat OK — %d task(s) pending", pending)
            else:
                log.debug("Heartbeat OK")

    # ── Task polling and execution ────────────────────────────────────────────

    async def _poll_and_execute(self) -> None:
        """Fetch up to 3 tasks and execute each sequentially."""
        async with _client(self.cfg) as client:
            resp = await client.get(
                "/api/connectors/tasks",
                params={"max_tasks": 3},
                headers=_connector_auth_header(self.cfg),
            )
            resp.raise_for_status()
            tasks = resp.json().get("tasks", [])

        for task in tasks:
            await self._execute_and_submit(task)

    async def _execute_and_submit(self, task: dict) -> None:
        task_id = task.get("task_id") or task.get("id")
        task_type = task.get("task_type", "unknown")
        parameters = task.get("parameters") or {}
        started_at = datetime.now(timezone.utc)

        log.info("Executing task %s (type=%s)", task_id, task_type)

        try:
            result_data = await self.executor.execute(task_type, parameters)
            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

            await self._submit_result(task_id, "completed", result_data, duration_ms)
            log.info("Task %s completed in %dms", task_id, duration_ms)

        except TaskError as exc:
            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            status = "failed" if not exc.recoverable else "failed"
            await self._submit_result(
                task_id, status, {}, duration_ms, error_message=str(exc)
            )
            log.error("Task %s failed: %s", task_id, exc)

        except Exception as exc:
            duration_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
            await self._submit_result(
                task_id, "failed", {}, duration_ms, error_message=f"Unexpected error: {exc}"
            )
            log.error("Task %s unexpected error: %s", task_id, exc)

    async def _submit_result(
        self,
        task_id: str,
        status: str,
        data: dict,
        duration_ms: int,
        error_message: str | None = None,
    ) -> None:
        rows = (
            len(data.get("customers") or data.get("invoices") or
                data.get("items") or data.get("payments") or data.get("vendors") or [])
        )
        body: dict = {
            "task_id": task_id,
            "company_id": self.cfg.company_id,
            "status": status,
            "data": data,
            "rows_returned": rows,
            "duration_ms": duration_ms,
        }
        if error_message:
            body["error_message"] = error_message

        async with _client(self.cfg) as client:
            resp = await client.post(
                "/api/connectors/task-result",
                json=body,
                headers=_connector_auth_header(self.cfg),
            )
            resp.raise_for_status()


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _client(cfg: ConnectorConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=cfg.cloud_url,
        timeout=30.0,
        headers={"User-Agent": f"SecretaryAI-Connector/{cfg.version}"},
    )


def _connector_auth_header(cfg: ConnectorConfig) -> dict:
    return {"Authorization": f"Bearer {cfg.connector_token}"}


def _user_auth_header(cfg: ConnectorConfig) -> dict:
    """For /register which requires a user JWT (stored in env or state)."""
    user_token = os.environ.get("SECRETARY_USER_TOKEN", "")
    return {"Authorization": f"Bearer {user_token}"} if user_token else {}


# ── Entry point ───────────────────────────────────────────────────────────────

import os  # noqa: E402 (after module-level code for clarity)


def main() -> None:
    cfg = ConnectorConfig()
    connector = Connector(cfg)
    try:
        asyncio.run(connector.run())
    except KeyboardInterrupt:
        log.info("Connector stopped by user.")


if __name__ == "__main__":
    main()
