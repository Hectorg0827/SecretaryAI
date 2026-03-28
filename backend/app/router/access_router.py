"""
Access Router — selects the least-fragile data path for each capability.

Priority order (configured per capability):
  API           → QB/Gmail/Sheets connectors
  PLAYWRIGHT    → headless browser automation
  FILE_INGESTION→ locally ingested CSV/Excel/PDF reports
  COMPUTER_USE  → vision-based last resort

For COMMIT operations: never auto-escalate past API without human approval.
Every attempt is logged to access_router_log with confidence score.
"""
import logging
import time
from enum import Enum
from typing import Optional

from app.domain.data_result import DataResult, Confidence
from app.domain.contracts import DataPath

log = logging.getLogger(__name__)


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

# Ordered fallback chains per capability
_PATH_CHAINS: dict[str, list[AccessPath]] = {
    "inventory": [AccessPath.API, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "orders": [AccessPath.API, AccessPath.FILE_INGESTION, AccessPath.PLAYWRIGHT, AccessPath.COMPUTER_USE],
    "customers": [AccessPath.API, AccessPath.COMPUTER_USE],
    "customs_status": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "distributor_orders": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
    "report_download": [AccessPath.PLAYWRIGHT, AccessPath.FILE_INGESTION, AccessPath.COMPUTER_USE],
}


class AccessRouter:
    def __init__(self, company_config: dict, db):
        self._config = company_config
        self._db = db
        self._company_id = company_config.get("id", "unknown")

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
        For COMMIT operations escalated past API, stop and raise requiring approval.
        """
        chain = _PATH_CHAINS.get(capability, [AccessPath.API, AccessPath.COMPUTER_USE])
        first_error: Optional[AccessPath] = None

        for i, path in enumerate(chain):
            # COMMIT escalation guard — never auto-promote to a less reliable path
            if operation_type == "commit" and i > 0 and path != AccessPath.API:
                log.warning(
                    "AccessRouter: COMMIT for %s would require escalation to %s — requiring approval",
                    capability, path.value,
                )
                raise CommitRequiresApprovalError(capability, path)

            data_path = _PATH_TO_DATAPATH[path]
            confidence = _PATH_CONFIDENCE[path]

            start = time.monotonic()
            try:
                data = await self._try_path(path, capability, params)
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
    # Path handlers
    # ------------------------------------------------------------------

    async def _try_path(self, path: AccessPath, capability: str, params: dict) -> dict:
        if path == AccessPath.API:
            return await self._try_api(capability, params)
        if path == AccessPath.PLAYWRIGHT:
            return await self._try_playwright(capability, params)
        if path == AccessPath.FILE_INGESTION:
            return await self._try_file_ingestion(capability, params)
        if path == AccessPath.COMPUTER_USE:
            return await self._try_computer_use(capability, params)
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

    async def _try_playwright(self, capability: str, params: dict) -> dict:
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

        async with PlaywrightRunner(self._config) as runner:
            return await runner.run_workflow(workflow_name, portal_params)

    async def _try_file_ingestion(self, capability: str, params: dict) -> dict:
        """Query the most recent ingested file record from DB for this capability."""
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
            return {
                "rows": row.get("data", []),
                "filename": row.get("filename", ""),
                "source": "file_ingestion",
            }
        except Exception as exc:
            raise RuntimeError(f"File ingestion lookup failed: {exc}") from exc

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
