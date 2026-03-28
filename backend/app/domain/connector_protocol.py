"""
Cloud ↔ Local Connector Protocol.

This module defines the message contracts between the SecretaryAI cloud control
plane and any locally-installed connector (e.g. the QB Desktop bridge).

Design goals:
  - The cloud is ALWAYS the source of truth.
  - The connector is a dumb bridge: it receives tasks, executes them, and
    reports results.  No business logic lives in the connector.
  - Messages are JSON-serialisable Pydantic models so they work over HTTPS
    REST and can be validated on both ends.
  - Every message carries company_id and correlation_id for tenant isolation
    and distributed tracing.

Protocol flow:
  1. REGISTER   — connector → cloud on startup
  2. HEARTBEAT  — connector → cloud every HEARTBEAT_INTERVAL_SECONDS
  3. FETCH_TASK — connector → cloud to poll for pending work
  4. SUBMIT_RESULT — connector → cloud after completing a task
  5. REPORT_STATUS — connector → cloud for sync progress
  6. REPORT_ERROR  — connector → cloud for non-task errors

All endpoints are under /api/connectors/ and require a connector auth token
(a long-lived JWT issued by the cloud during registration).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# Connectors should send a heartbeat at least this often
HEARTBEAT_INTERVAL_SECONDS = 60
# Cloud marks connector "stale" after this many seconds without a heartbeat
STALE_THRESHOLD_SECONDS = 180  # 3 × interval


# ─── Enums ────────────────────────────────────────────────────────────────────

class ConnectorType(str, Enum):
    QB_DESKTOP  = "qb_desktop"
    QB_ONLINE   = "qb_online"
    GOOGLE      = "google"
    SHOPIFY     = "shopify"
    FILE_WATCHER = "file_watcher"
    CUSTOM      = "custom"


class TaskType(str, Enum):
    """Tasks the cloud can dispatch to a connector."""
    # Data reads
    FETCH_CUSTOMERS     = "fetch_customers"
    FETCH_INVOICES      = "fetch_invoices"
    FETCH_ITEMS         = "fetch_items"
    FETCH_PAYMENTS      = "fetch_payments"
    FETCH_PURCHASE_ORDERS = "fetch_purchase_orders"
    # Data writes (require policy approval before being dispatched)
    CREATE_INVOICE      = "create_invoice"
    CREATE_PO           = "create_po"
    UPDATE_ITEM         = "update_item"
    # File operations
    UPLOAD_FILE         = "upload_file"
    WATCH_FOLDER        = "watch_folder"
    # Diagnostics
    PING                = "ping"
    GET_VERSION         = "get_version"
    GET_CAPABILITIES    = "get_capabilities"


class TaskStatus(str, Enum):
    PENDING    = "pending"
    DISPATCHED = "dispatched"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    TIMED_OUT  = "timed_out"


# ─── Registration ─────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    """
    Sent by the connector on first startup (or when the cloud token is lost).
    The cloud validates the install_secret, creates a connector_registration
    row, and returns a long-lived connector_token.
    """
    company_id: str
    connector_type: ConnectorType
    connector_id: str = Field(
        description="Stable unique ID for this installation, e.g. hostname + random UUID"
    )
    version: str
    install_secret: str = Field(
        description="Pre-shared secret set during install; validates this is a legitimate install"
    )
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterResponse(BaseModel):
    registration_id: str
    connector_token: str  # JWT issued by cloud; used for all subsequent calls
    task_poll_interval_seconds: int = 30
    heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS
    cloud_api_base: str
    message: str = "registered"


# ─── Heartbeat ────────────────────────────────────────────────────────────────

class HeartbeatRequest(BaseModel):
    """
    Sent every HEARTBEAT_INTERVAL_SECONDS.
    The cloud updates last_heartbeat and resolves status='stale' if present.
    """
    registration_id: str
    status: str = "ok"           # 'ok' | 'degraded' | 'error'
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional: {qb_connection: bool, last_sync_ago_sec: int, ...}"
    )


class HeartbeatResponse(BaseModel):
    acknowledged: bool = True
    server_time: datetime
    pending_task_count: int = 0  # hint to connector that tasks are waiting


# ─── Task dispatch ────────────────────────────────────────────────────────────

class ConnectorTask(BaseModel):
    """
    A unit of work the cloud dispatches to the connector.
    Connectors call FETCH_TASK to poll; the cloud returns the next pending task.
    """
    task_id: str
    task_type: TaskType
    company_id: str
    correlation_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    priority: int = 5            # 1=highest, 10=lowest
    timeout_seconds: int = 120
    issued_at: datetime
    expires_at: datetime | None = None


class FetchTaskRequest(BaseModel):
    registration_id: str
    max_tasks: int = 1           # how many tasks to pull in one poll


class FetchTaskResponse(BaseModel):
    tasks: list[ConnectorTask] = Field(default_factory=list)
    server_time: datetime


# ─── Task result ──────────────────────────────────────────────────────────────

class TaskResult(BaseModel):
    """
    Submitted by the connector after completing (or failing) a task.
    """
    task_id: str
    registration_id: str
    company_id: str
    correlation_id: str
    status: TaskStatus
    # Normalised data payload; shape depends on task_type
    data: dict[str, Any] | None = None
    # Pagination: if has_more=True, the cloud will dispatch another task
    # with offset=next_offset to continue fetching
    has_more: bool = False
    next_offset: int | None = None
    rows_returned: int = 0
    error_message: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    completed_at: datetime | None = None


class SubmitResultResponse(BaseModel):
    acknowledged: bool = True
    next_task: ConnectorTask | None = None   # optional: cloud piggybacks next task


# ─── Sync status ──────────────────────────────────────────────────────────────

class SyncStatusReport(BaseModel):
    """
    Progress update for a long-running sync operation.
    Can be sent multiple times during a fetch_all operation.
    """
    registration_id: str
    company_id: str
    sync_type: str          # 'full' | 'incremental' | 'on_demand'
    entity_type: str        # 'customers' | 'invoices' | ...
    rows_fetched: int = 0
    rows_upserted: int = 0
    rows_errored: int = 0
    is_final: bool = False  # True on the last report for this sync
    error_detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SyncStatusResponse(BaseModel):
    acknowledged: bool = True


# ─── Error reporting ──────────────────────────────────────────────────────────

class ConnectorErrorReport(BaseModel):
    """
    Reports a non-task error from the connector (e.g. QB Desktop crash,
    lost network, credential expiry).
    """
    registration_id: str
    company_id: str
    error_type: str          # 'qb_connection' | 'credential_expired' | 'network' | 'unknown'
    error_message: str
    recoverable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorErrorResponse(BaseModel):
    acknowledged: bool = True
    remediation_hint: str | None = None


# ─── Capability registry ──────────────────────────────────────────────────────

# Capabilities that a QB Desktop connector instance can advertise.
# These map to the capability keys used by AccessRouter.
QB_DESKTOP_CAPABILITIES = [
    "customers",
    "invoices",
    "payments",
    "inventory",
    "purchase_orders",
    "vendors",
    "chart_of_accounts",
    "journal_entries",
]

# All file-watcher capabilities
FILE_WATCHER_CAPABILITIES = [
    "file_watch_inventory",
    "file_watch_orders",
    "file_watch_reports",
]
