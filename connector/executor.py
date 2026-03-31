"""
Task Executor — runs connector tasks against QuickBooks Desktop.

Each task has a task_type (e.g. 'fetch_customers', 'fetch_invoices').
The executor calls the appropriate QB adapter method and returns a result dict.

All QB API calls use the Conductor SDK (QB Desktop REST proxy).
"""
import logging
from datetime import date, timedelta
from typing import Any

log = logging.getLogger(__name__)

# Supported task types
SUPPORTED_TASKS = {
    "fetch_customers",
    "fetch_invoices",
    "fetch_inventory",
    "fetch_payments",
    "fetch_vendors",
    "ping",  # health-check — no QB call needed
}


class TaskExecutor:
    def __init__(self, conductor_api_key: str, conductor_end_user_id: str):
        self._api_key = conductor_api_key
        self._end_user_id = conductor_end_user_id
        self._adapter = None

    def _get_adapter(self):
        """Lazy-init the QB Desktop adapter."""
        if self._adapter is None:
            from backend.app.connectors.qb_desktop import QBDesktopAdapter
            self._adapter = QBDesktopAdapter(
                api_key=self._api_key,
                end_user_id=self._end_user_id,
            )
        return self._adapter

    async def execute(self, task_type: str, parameters: dict) -> dict:
        """
        Execute a task and return a result dict.
        Raises TaskError on unrecoverable failures.
        """
        if task_type not in SUPPORTED_TASKS:
            raise TaskError(f"Unsupported task type: {task_type}", recoverable=False)

        if task_type == "ping":
            return {"status": "pong", "task_type": "ping"}

        if task_type == "fetch_customers":
            return await self._fetch_customers(parameters)

        if task_type == "fetch_invoices":
            return await self._fetch_invoices(parameters)

        if task_type == "fetch_inventory":
            return await self._fetch_inventory(parameters)

        if task_type == "fetch_payments":
            return await self._fetch_payments(parameters)

        if task_type == "fetch_vendors":
            return await self._fetch_vendors(parameters)

        raise TaskError(f"No handler for task_type={task_type}", recoverable=False)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _fetch_customers(self, params: dict) -> dict:
        try:
            qb = self._get_adapter()
            customers = await qb.get_customers()
            return {
                "customers": [_serialize(c) for c in customers],
                "count": len(customers),
            }
        except Exception as exc:
            raise TaskError(f"fetch_customers failed: {exc}", recoverable=True) from exc

    async def _fetch_invoices(self, params: dict) -> dict:
        try:
            qb = self._get_adapter()
            days = int(params.get("days", 30))
            date_to = date.today()
            date_from = date_to - timedelta(days=days)
            invoices = await qb.get_invoices(date_from, date_to)
            return {
                "invoices": [_serialize(i) for i in invoices],
                "count": len(invoices),
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
            }
        except Exception as exc:
            raise TaskError(f"fetch_invoices failed: {exc}", recoverable=True) from exc

    async def _fetch_inventory(self, params: dict) -> dict:
        try:
            qb = self._get_adapter()
            items = await qb.get_inventory()
            return {
                "items": [_serialize(i) for i in items],
                "count": len(items),
            }
        except Exception as exc:
            raise TaskError(f"fetch_inventory failed: {exc}", recoverable=True) from exc

    async def _fetch_payments(self, params: dict) -> dict:
        try:
            qb = self._get_adapter()
            # QB Desktop adapter may not have get_payments; graceful fallback
            if hasattr(qb, "get_payments"):
                payments = await qb.get_payments()
                return {"payments": [_serialize(p) for p in payments], "count": len(payments)}
            return {"payments": [], "count": 0, "note": "payments not supported by this adapter"}
        except Exception as exc:
            raise TaskError(f"fetch_payments failed: {exc}", recoverable=True) from exc

    async def _fetch_vendors(self, params: dict) -> dict:
        try:
            qb = self._get_adapter()
            if hasattr(qb, "get_vendors"):
                vendors = await qb.get_vendors()
                return {"vendors": [_serialize(v) for v in vendors], "count": len(vendors)}
            return {"vendors": [], "count": 0, "note": "vendors not supported by this adapter"}
        except Exception as exc:
            raise TaskError(f"fetch_vendors failed: {exc}", recoverable=True) from exc


def _serialize(obj: Any) -> dict:
    """Convert a dataclass / object to a JSON-safe dict."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                for k, v in vars(obj).items()}
    return {"value": str(obj)}


class TaskError(Exception):
    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable
