"""
SecretaryAI QB Desktop Connector — main entry point.

Usage:
  python -m connector.main          (from a source checkout)
  SecretaryAIConnector.exe         (packaged; see connector/connector.spec)

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
import os
import sys
import time
from datetime import datetime, timezone

import httpx

from connector import __version__
from connector.config import ConfigError, ConnectorConfig
from connector.executor import TaskExecutor, TaskError

def _configure_logging() -> "Path | None":
    """
    Log to a rotating file as well as the console.

    A packaged connector runs in the background with no console attached, so
    console-only logging means a customer whose sync is failing has nothing to
    send to support. The file lives beside the state file in per-user appdata.

    `sys.stdout` is None in a PyInstaller windowed build, and handing None to
    StreamHandler raises at import time, so the console handler is conditional.
    """
    from logging.handlers import RotatingFileHandler
    from connector.config import default_state_dir

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if sys.stdout is not None:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    log_path = None
    try:
        d = default_state_dir()
        d.mkdir(parents=True, exist_ok=True)
        log_path = d / "connector.log"
        fh = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        # An unwritable appdata directory must not stop the connector running.
        log_path = None
    return log_path


_LOG_PATH = _configure_logging()
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


def selfcheck() -> int:
    """
    Verify a packaged connector is intact, without needing cloud credentials.

    This exists because a PyInstaller build can succeed and still produce an
    executable that fails every QuickBooks task at runtime: the Conductor
    adapter is reached through a dynamic import, so if the spec's hidden
    imports are wrong the analyser silently omits it and the breakage only
    appears when a customer's first sync fails. CI runs this against the built
    executable so that can never ship again, and support can ask a customer to
    run it to tell a broken install apart from a configuration problem.

    Returns 0 if everything needed is present.
    """
    print(f"SecretaryAI Connector {__version__}")
    print(f"frozen bundle: {bool(getattr(sys, 'frozen', False))}")
    ok = True

    try:
        from connector.qb_adapter import load_qb_desktop_adapter

        adapter = load_qb_desktop_adapter()
        print(f"  [ok]   QuickBooks Desktop adapter: {adapter.__module__}.{adapter.__name__}")
    except Exception as exc:
        print(f"  [FAIL] QuickBooks Desktop adapter could not be loaded: {exc}")
        ok = False

    try:
        import httpx  # noqa: F401

        print("  [ok]   httpx available")
    except Exception as exc:
        print(f"  [FAIL] httpx missing: {exc}")
        ok = False

    try:
        from connector.config import default_state_dir

        d = default_state_dir()
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        print(f"  [ok]   state directory writable: {d}")
    except Exception as exc:
        print(f"  [FAIL] state directory not writable: {exc}")
        ok = False

    print("selfcheck: PASS" if ok else "selfcheck: FAIL")
    return 0 if ok else 1


def main() -> int:
    """
    Start the connector. Returns a process exit code.

    Missing configuration exits 2 with the message the user needs, rather than
    a KeyError traceback. A packaged connector is often launched with no console
    attached, so the message is also written to the log file the desktop app
    reads when it reports connector status.
    """
    if "--selfcheck" in sys.argv[1:]:
        return selfcheck()

    try:
        cfg = ConnectorConfig()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    connector = Connector(cfg)
    log.info("State file: %s", cfg.state_file)
    if _LOG_PATH:
        log.info("Log file:   %s", _LOG_PATH)
    try:
        asyncio.run(connector.run())
    except KeyboardInterrupt:
        log.info("Connector stopped by user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
