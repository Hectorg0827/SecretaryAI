"""
Access Router — selects the least-fragile data path for each capability.

Priority order (per-company configurable via company_features.capability_routes):
  API           → QB/Gmail/Sheets connectors
  PLAYWRIGHT    → headless browser automation
  FILE_INGESTION→ locally ingested CSV/Excel/PDF reports
  COMPUTER_USE  → vision-based last resort

For COMMIT operations: never auto-escalate past API without human approval.
Every attempt is logged to access_router_log with confidence + correlation_id.

Phase 4 additions:
  - Per-company capability route overrides loaded from company_features DB
  - browser_jobs rows written for every Playwright attempt
  - Stale file confidence: FILE_STALE (45) when ingested file is >24h old
  - correlation_id threaded into browser_jobs and log rows
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.domain.data_result import DataResult, Confidence
from app.domain.contracts import DataPath

log = logging.getLogger(__name__)

# Age threshold for degrading file confidence from FILE_FRESH to FILE_STALE
_FILE_STALE_HOURS = 24


class AccessPath(Enum):
    API = "api"
    PLAYWRIGHT = "playwright"
    FILE_INGESTION = "file_ingestion"
    WINDOWS_UI = "windows_ui"
    COMPUTER_USE = "computer_use"


# Confidence score per access path
_PATH_CONFIDENCE: dict[AccessPath, int] = {
    AccessPath.API: Confidence.API,
    AccessPath.PLAYWRIGHT: Confidence.BROWSER,
    AccessPath.FILE_INGESTION: Confidence.FILE_FRESH,
    AccessPath.WINDOWS_UI: Confidence.COMPUTER_USE,
    AccessPath.COMPUTER_USE: Confidence.COMPUTER_USE,
}

# Map AccessPath → DataPath (domain contract)
_PATH_TO_DATAPATH: dict[AccessPath, DataPath] = {
    AccessPath.API: DataPath.API,
    AccessPath.PLAYWRIGHT: DataPath.BROWSER,
    AccessPath.FILE_INGESTION: DataPath.FILE,
    AccessPath.WINDOWS_UI: DataPath.COMPUTER_USE,
    AccessPath.COMPUTER_USE: DataPath.COMPUTER_USE,
}

# Built-in fallback chains (used when company has no per-capability override)
_DEFAULT_PATH_CHAINS: dict[str, list[AccessPath]] = {
    "inventory": [AccessPath.API, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "orders": [AccessPath.API, AccessPath.FILE_INGESTION, AccessPath.PLAYWRIGHT, AccessPath.COMPUTER_USE],
    "customers": [AccessPath.API, AccessPath.COMPUTER_USE],
    "customs_status": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "distributor_orders": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "report_download": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
}

# Valid path names accepted from DB overrides
_VALID_PATH_NAMES = {p.value for p in AccessPath}


class AccessRouter:
    def __init__(self, company_config: dict, db):
        self._config = company_config
        self._db = db
        self._company_id = company_config.get("id", "unknown")
        # In-request cache for DB-loaded capability overrides
        self._capability_overrides: dict[str, list[AccessPath]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def route(
        self,
        capability: str,
        params: dict,
        operation_type: str = "read",
        correlation_id: Optional[str] = None,
    ) -> DataResult:
        """
        Try each path in priority order and return a DataResult from the first success.
        Path chains are loaded from company_features.capability_routes (with fallback
        to built-in defaults).

        For COMMIT operations escalated past API, raises CommitRequiresApprovalError.
        All paths failing returns DataResult.from_error() (never raises for reads).
        """
        chain = await self._resolve_chain(capability)
        first_error: Optional[AccessPath] = None

        for i, path in enumerate(chain):
            # COMMIT escalation guard
            if operation_type == "commit" and i > 0 and path != AccessPath.API:
                log.warning(
                    "AccessRouter: COMMIT for %s would require escalation to %s — requiring approval",
                    capability, path.value,
                )
                raise CommitRequiresApprovalError(capability, path)

            data_path = _PATH_TO_DATAPATH[path]

            start = time.monotonic()
            try:
                data, confidence = await self._try_path_with_confidence(
                    path, capability, params, correlation_id
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                self._log(capability, operation_type, path, True, None, duration_ms, i > 0, confidence, correlation_id)
                return DataResult.ok(
                    data=data,
                    source=data_path,
                    confidence=confidence,
                    capability=capability,
                    correlation_id=correlation_id,
                    metadata={"fallback_used": i > 0, "duration_ms": duration_ms},
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                if first_error is None:
                    first_error = path
                log.warning(
                    "AccessRouter: %s via %s failed (%s), trying next path",
                    capability, path.value, exc,
                )
                self._log(capability, operation_type, path, False, str(exc), duration_ms, False, 0, correlation_id)

        return DataResult.from_error(
            error=f"All paths failed for '{capability}': {[p.value for p in chain]}",
            source=DataPath.UNKNOWN,
            capability=capability,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------
    # Capability registry (DB-backed with built-in fallback)
    # ------------------------------------------------------------------

    async def _resolve_chain(self, capability: str) -> list[AccessPath]:
        """
        Return the path chain for this capability.
        Checks company_features.capability_routes first; falls back to built-in defaults.
        """
        overrides = await self._load_capability_overrides()
        if capability in overrides:
            return overrides[capability]
        return _DEFAULT_PATH_CHAINS.get(capability, [AccessPath.API, AccessPath.COMPUTER_USE])

    async def _load_capability_overrides(self) -> dict[str, list[AccessPath]]:
        """
        Load per-company capability_routes from company_features.
        Result is cached for the lifetime of this router instance (per-request).
        """
        if self._capability_overrides is not None:
            return self._capability_overrides

        try:
            result = (
                self._db.table("company_features")
                .select("capability_routes")
                .eq("company_id", self._company_id)
                .execute()
            )
            raw: dict = {}
            if result.data:
                raw = result.data[0].get("capability_routes") or {}
        except Exception as exc:
            log.warning(
                "AccessRouter: could not load capability_routes for %s: %s — using defaults",
                self._company_id, exc,
            )
            raw = {}

        parsed: dict[str, list[AccessPath]] = {}
        for cap, paths in raw.items():
            if not isinstance(paths, list):
                continue
            valid = [AccessPath(p) for p in paths if p in _VALID_PATH_NAMES]
            if valid:
                parsed[cap] = valid

        self._capability_overrides = parsed
        return parsed

    # ------------------------------------------------------------------
    # Path handlers (with confidence overrides)
    # ------------------------------------------------------------------

    async def _try_path_with_confidence(
        self,
        path: AccessPath,
        capability: str,
        params: dict,
        correlation_id: Optional[str],
    ) -> tuple[dict, int]:
        """
        Attempt a path and return (data, confidence).
        FILE_INGESTION may downgrade confidence to FILE_STALE if data is >24h old.
        """
        if path == AccessPath.FILE_INGESTION:
            data, confidence = await self._try_file_ingestion(capability, params)
            return data, confidence
        if path == AccessPath.PLAYWRIGHT:
            data = await self._try_playwright(capability, params, correlation_id)
            return data, _PATH_CONFIDENCE[path]
        if path == AccessPath.API:
            data = await self._try_api(capability, params)
            return data, _PATH_CONFIDENCE[path]
        if path == AccessPath.COMPUTER_USE:
            return await self._try_computer_use_isolated(capability, params, correlation_id)
        raise ValueError(f"Unhandled path: {path}")

    async def _try_api(self, capability: str, params: dict) -> dict:
        from app.connectors.qb_desktop import QBDesktopAdapter
        from app.connectors.qb_online import QBOnlineAdapter
        from app.connectors.base import QuickBooksAdapter

        cfg = self._config
        qb_type = cfg.get("qb_type")
        if not qb_type:
            raise RuntimeError("No QB connector configured")

        if qb_type == "desktop":
            qb: QuickBooksAdapter = QBDesktopAdapter(
                api_key=cfg["conductor_api_key"],
                end_user_id=cfg["conductor_end_user_id"],
            )
        else:
            qb = QBOnlineAdapter(
                client_id=cfg["qbo_client_id"],
                client_secret=cfg["qbo_client_secret"],
                access_token=cfg["qbo_access_token"],
                refresh_token=cfg["qbo_refresh_token"],
                realm_id=cfg["qbo_realm_id"],
                on_token_refresh=None,
            )

        if capability == "inventory":
            items = await qb.get_inventory()
            return {"items": [vars(i) if hasattr(i, "__dict__") else i for i in items]}
        if capability == "orders":
            from datetime import date, timedelta
            days = params.get("days", 30)
            date_to = date.today()
            date_from = date_to - timedelta(days=days)
            invoices = await qb.get_invoices(date_from, date_to)
            return {"invoices": [vars(i) if hasattr(i, "__dict__") else i for i in invoices]}
        if capability == "customers":
            customers = await qb.get_customers()
            return {"customers": [vars(c) if hasattr(c, "__dict__") else c for c in customers]}
        raise RuntimeError(f"API path does not support capability '{capability}'")

    async def _try_playwright(
        self,
        capability: str,
        params: dict,
        correlation_id: Optional[str] = None,
    ) -> dict:
        from app.playwright_runner.runner import PlaywrightRunner

        workflow_map = {
            "customs_status": "customs_portal",
            "distributor_orders": "distributor_portal",
            "report_download": "distributor_portal",
        }
        workflow_name = workflow_map.get(capability)
        if not workflow_name:
            raise RuntimeError(f"No Playwright workflow for capability '{capability}'")

        portal_params = dict(params)
        if capability == "customs_status" and not portal_params.get("portal_url"):
            portal_params["portal_url"] = self._config.get("customs_portal_url", "")
            portal_params.setdefault("username", self._config.get("customs_portal_username", ""))
            portal_params.setdefault("password", self._config.get("customs_portal_password", ""))

        job_id = self._start_browser_job(workflow_name, capability, portal_params, correlation_id)
        try:
            async with PlaywrightRunner(self._config) as runner:
                result = await runner.run_workflow(workflow_name, portal_params)
            self._finish_browser_job(job_id, "completed", result, confidence=Confidence.BROWSER)
            return result
        except Exception as exc:
            self._finish_browser_job(job_id, "failed", error_message=str(exc))
            raise

    async def _try_file_ingestion(self, capability: str, params: dict) -> tuple[dict, int]:
        """
        Query the most recent ingested file and return (data, confidence).
        Confidence is downgraded to FILE_STALE if the file is older than _FILE_STALE_HOURS.
        """
        report_type_map = {
            "inventory": "inventory",
            "orders": "orders",
            "distributor_orders": "orders",
            "report_download": "unknown",
            "customs_status": "customs",
        }
        report_type = report_type_map.get(capability, capability)

        try:
            result = (
                self._db.table("ingested_files")
                .select("data, filename, created_at")
                .eq("company_id", self._company_id)
                .eq("report_type", report_type)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = result.data or []
            if not rows:
                raise RuntimeError(f"No ingested file found for report_type '{report_type}'")

            row = rows[0]
            data = {
                "rows": row.get("data", []),
                "filename": row.get("filename", ""),
                "source": "file_ingestion",
            }

            # Determine staleness
            confidence = self._file_confidence(row.get("created_at"))
            return data, confidence

        except Exception as exc:
            raise RuntimeError(f"File ingestion lookup failed: {exc}") from exc

    async def _try_computer_use_isolated(
        self,
        capability: str,
        params: dict,
        correlation_id: Optional[str],
    ) -> tuple[dict, int]:
        """
        Non-blocking CU path — checks the cache, enqueues on miss.

        Returns (data, confidence) if a fresh cached result is available.
        Raises RuntimeError (treated as a path miss by the router) if:
          - no cached result exists (a job has been enqueued for next time), or
          - the DB is unavailable.

        This prevents the request path from ever blocking on a live CU session.
        """
        from app.services.computer_use_service import ComputerUseService
        cu = ComputerUseService(self._db, self._company_id)
        cached = cu.get_or_enqueue(capability, params, correlation_id=correlation_id)
        if cached is None:
            raise RuntimeError(
                f"CU: no cached result for capability={capability}; "
                "job enqueued for background execution"
            )
        data, confidence = cached
        log.debug(
            "CU: returning cached result for capability=%s confidence=%d",
            capability, confidence,
        )
        return data, confidence

    async def _try_computer_use(self, capability: str, params: dict) -> dict:
        from app.computer_use.engine import ComputerUseEngine
        engine = ComputerUseEngine(company_config=self._config, db=self._db)

        task_map = {
            "inventory": "Get current inventory quantities for all products",
            "orders": "Get recent order history from the ordering system",
            "customers": "Get customer list from the system",
            "customs_status": "Get status of all active shipments/containers",
            "distributor_orders": "Get recent distributor orders",
            "report_download": "Download the most recent report",
        }
        task = task_map.get(capability, f"Get data for {capability}")
        app_name = self._config.get("ordering_system_app", "the application")
        return await engine.extract_data(app_name=app_name, task=task)

    # ------------------------------------------------------------------
    # Browser job tracking
    # ------------------------------------------------------------------

    def _start_browser_job(
        self,
        workflow_name: str,
        capability: str,
        params: dict,
        correlation_id: Optional[str],
    ) -> Optional[str]:
        """Insert a browser_jobs row with status='running'. Returns the job ID or None."""
        try:
            job_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            row: dict = {
                "id": job_id,
                "company_id": self._company_id,
                "workflow_name": workflow_name,
                "capability": capability,
                "parameters": params,
                "status": "running",
                "queued_at": now,
                "started_at": now,
            }
            if correlation_id:
                row["correlation_id"] = correlation_id
            self._db.table("browser_jobs").insert(row).execute()
            return job_id
        except Exception as exc:
            log.debug("AccessRouter: could not write browser_jobs start row: %s", exc)
            return None

    def _finish_browser_job(
        self,
        job_id: Optional[str],
        status: str,
        result: Optional[dict] = None,
        error_message: Optional[str] = None,
        confidence: int = 0,
    ) -> None:
        """Update the browser_jobs row with final status and result."""
        if not job_id:
            return
        try:
            update: dict = {
                "status": status,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            if result is not None:
                update["result"] = result
                update["confidence_pct"] = confidence
            if error_message:
                update["error_message"] = error_message
            self._db.table("browser_jobs").update(update).eq("id", job_id).execute()
        except Exception as exc:
            log.debug("AccessRouter: could not update browser_jobs row %s: %s", job_id, exc)

    # ------------------------------------------------------------------
    # Confidence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_confidence(created_at_iso: Optional[str]) -> int:
        """
        Return FILE_FRESH or FILE_STALE depending on the age of the ingested file.
        Stale threshold: _FILE_STALE_HOURS hours.
        """
        if not created_at_iso:
            return Confidence.FILE_STALE

        try:
            # Parse ISO string — handle both 'Z' and '+00:00' suffixes
            ts_str = created_at_iso.replace("Z", "+00:00")
            file_time = datetime.fromisoformat(ts_str)
            age = datetime.now(timezone.utc) - file_time
            if age > timedelta(hours=_FILE_STALE_HOURS):
                return Confidence.FILE_STALE
            return Confidence.FILE_FRESH
        except Exception:
            return Confidence.FILE_STALE

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log(
        self,
        capability: str,
        operation_type: str,
        path: AccessPath,
        success: bool,
        error_msg: Optional[str],
        duration_ms: int,
        fallback_used: bool,
        confidence: int,
        correlation_id: Optional[str],
    ) -> None:
        try:
            self._db.table("access_router_log").insert(
                {
                    "company_id": self._company_id,
                    "capability": capability,
                    "operation_type": operation_type,
                    "path_tried": path.value,
                    "success": success,
                    "fallback_used": fallback_used,
                    "error_msg": error_msg,
                    "duration_ms": duration_ms,
                    "confidence": confidence,
                    "correlation_id": correlation_id,
                }
            ).execute()
        except Exception as exc:
            log.debug("AccessRouter: could not write router log: %s", exc)


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class CommitRequiresApprovalError(Exception):
    def __init__(self, capability: str, blocked_at: AccessPath):
        self.capability = capability
        self.blocked_at = blocked_at
        super().__init__(
            f"COMMIT for '{capability}' cannot auto-escalate to {blocked_at.value}; requires human approval"
        )


class AllPathsFailedError(Exception):
    def __init__(self, capability: str, chain: list[AccessPath]):
        self.capability = capability
        self.chain = chain
        super().__init__(
            f"All paths failed for '{capability}': {[p.value for p in chain]}"
        )
