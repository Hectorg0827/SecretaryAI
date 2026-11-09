"""Lola BI result and source-snapshot contracts (Workstream 2).

Every number Lola states must be traceable to a ``SourceSnapshot`` with a
tenant, provider, fetch time, coverage and status. A failed, unconfigured or
unauthorized read is never an empty success: callers get a snapshot whose
``usable`` is False and analytics raise ``SourceUnavailable`` instead of
returning zero.

This module is pure: no provider, network or database access.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping

CONTRACT_VERSION = "lola.bi.v1"


class SourceStatus(str, Enum):
    OK = "ok"                          # read completed, provider contract satisfied
    PARTIAL = "partial"                # read completed but coverage is known to be limited
    FAILED = "failed"                  # provider/network/malformed-data failure
    AUTH_FAILED = "auth_failed"        # OAuth token rejected or refresh failed
    NOT_CONFIGURED = "not_configured"  # tenant has no connector for this source
    NOT_SUPPORTED = "not_supported"    # provider has no read path for this data
    DENIED = "denied"                  # caller scope does not permit this source


class SourceQuality(str, Enum):
    PROVIDER_OBSERVED = "provider_observed"     # validated provider response
    UNVERIFIED_MAPPING = "unverified_mapping"   # provider field mapping lacks vendor fixtures
    TENANT_DECLARED = "tenant_declared"         # configuration supplied by the tenant
    DERIVED = "derived"                         # computed from other snapshots


USABLE_STATUSES = frozenset({SourceStatus.OK, SourceStatus.PARTIAL})


class SourceUnavailable(RuntimeError):
    """A required source cannot support an answer; no figure may be produced."""

    def __init__(self, source: str, status: str, reason: str | None = None):
        self.source = source
        self.status = status
        self.reason = reason
        super().__init__(f"{source} unavailable ({status})")


class BIAccessDenied(PermissionError):
    """Caller scope does not include the requested capability or record."""


class TenantMismatch(PermissionError):
    """A record, cache entry or connector belongs to another tenant."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SourceSnapshot:
    """One read of one source for one tenant (``SourceSnapshot[T]`` in WS2 docs)."""

    tenant_id: str
    source: str
    provider: str
    status: SourceStatus
    records: tuple = ()
    fetched_at: datetime | None = None
    coverage: str = "unknown"
    window_from: date | None = None
    window_to: date | None = None
    quality: SourceQuality = SourceQuality.PROVIDER_OBSERVED
    warnings: tuple[str, ...] = ()
    error: str | None = None
    max_age: timedelta = timedelta(hours=24)
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise TenantMismatch("Snapshot requires a tenant id")
        if self.status in USABLE_STATUSES:
            if self.fetched_at is None or self.fetched_at.tzinfo is None:
                raise ValueError("A usable snapshot needs a timezone-aware fetched_at")
        elif self.records:
            raise ValueError("A failed snapshot cannot carry records")

    @property
    def usable(self) -> bool:
        return self.status in USABLE_STATUSES

    def age_seconds(self, now: datetime | None = None) -> float | None:
        if self.fetched_at is None:
            return None
        return ((now or utcnow()) - self.fetched_at).total_seconds()

    def is_stale(self, now: datetime | None = None) -> bool:
        age = self.age_seconds(now)
        return age is None or age > self.max_age.total_seconds() or age < -60

    def require(self, now: datetime | None = None) -> tuple:
        """Return records or raise; stale data is not silently current."""
        if not self.usable:
            raise SourceUnavailable(self.source, self.status.value, self.error)
        if self.is_stale(now):
            raise SourceUnavailable(self.source, "stale", "source older than allowed age")
        return self.records

    def with_records(self, records: Iterable, **changes: Any) -> "SourceSnapshot":
        return dataclasses.replace(self, records=tuple(records), **changes)

    def describe(self, now: datetime | None = None) -> dict:
        age = self.age_seconds(now)
        return {
            "source": self.source,
            "provider": self.provider,
            "status": self.status.value,
            "quality": self.quality.value,
            "coverage": self.coverage,
            "window": (
                {"from": self.window_from.isoformat() if self.window_from else None,
                 "to": self.window_to.isoformat() if self.window_to else None}
                if (self.window_from or self.window_to) else None
            ),
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "age_seconds": round(age, 1) if age is not None else None,
            "stale": self.is_stale(now) if self.usable else None,
            "record_count": len(self.records) if self.usable else None,
            "warnings": list(self.warnings),
            "error": self.error,
        }

    @classmethod
    def failed(cls, tenant_id: str, source: str, provider: str, status: SourceStatus,
               error: str, **kw: Any) -> "SourceSnapshot":
        if status in USABLE_STATUSES:
            raise ValueError("failed() requires a non-usable status")
        return cls(tenant_id=tenant_id, source=source, provider=provider,
                   status=status, error=error, **kw)


def jsonable(value: Any) -> Any:
    """Serialize for tool output: Decimal → exact string, dates → ISO."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    return value


@dataclass
class Finding:
    """A statement Lola may make. ``basis`` separates proof from conjecture."""

    statement: str
    basis: str  # "verified" (deterministic from sources) | "hypothesis"
    evidence: dict = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.basis not in ("verified", "hypothesis"):
            raise ValueError("Finding basis must be 'verified' or 'hypothesis'")


class ResultBuilder:
    """Collects provenance and data-quality notes while a tool runs."""

    def __init__(self, tool: str, department: str, scope: Any, *, now: datetime | None = None):
        self.tool = tool
        self.department = department
        self.scope = scope
        self.now = now or utcnow()
        self._sources: dict[str, SourceSnapshot] = {}
        self.warnings: list[str] = []
        self.missing: list[dict] = []

    def use(self, snapshot: SourceSnapshot, *, required: bool = True) -> tuple:
        """Register a snapshot; return its records or raise if required."""
        if snapshot.tenant_id != self.scope.tenant_id:
            raise TenantMismatch("Snapshot tenant does not match caller scope")
        self._sources[snapshot.source] = snapshot
        if snapshot.usable and not snapshot.is_stale(self.now):
            if snapshot.status is SourceStatus.PARTIAL:
                self.note_missing(snapshot.source, "partial_coverage", snapshot.coverage)
            if snapshot.quality is SourceQuality.UNVERIFIED_MAPPING:
                self.note_missing(snapshot.source, "unverified_mapping",
                                  "provider field mapping lacks vendor-backed fixtures")
            return snapshot.records
        if required:
            return snapshot.require(self.now)
        status = "stale" if snapshot.usable else snapshot.status.value
        self.note_missing(snapshot.source, status, snapshot.error)
        return ()

    def optional(self, snapshot: SourceSnapshot) -> tuple | None:
        records = self.use(snapshot, required=False)
        ok = snapshot.usable and not snapshot.is_stale(self.now)
        return records if ok else None

    def note_missing(self, what: str, status: str, detail: str | None = None) -> None:
        entry = {"item": what, "status": status, "detail": detail}
        if entry not in self.missing:
            self.missing.append(entry)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def sources(self) -> list[dict]:
        return [snap.describe(self.now) for snap in self._sources.values()]

    def finish(self, answer: Any, *, findings: list[Finding] | None = None,
               status: str | None = None) -> dict:
        if status is None:
            status = "partial" if (self.missing or self.warnings) else "ok"
        return envelope(
            tool=self.tool, department=self.department, scope=self.scope,
            status=status, answer=answer, sources=self.sources(),
            missing=self.missing, warnings=self.warnings,
            findings=findings or [], now=self.now,
        )

    def unavailable(self, exc: SourceUnavailable) -> dict:
        self.note_missing(exc.source, exc.status, exc.reason)
        return envelope(
            tool=self.tool, department=self.department, scope=self.scope,
            status="unavailable", answer=None, sources=self.sources(),
            missing=self.missing, warnings=self.warnings, findings=[], now=self.now,
        )


def envelope(*, tool: str, department: str, scope: Any, status: str, answer: Any,
             sources: list[dict], missing: list[dict], warnings: list[str],
             findings: list[Finding], now: datetime) -> dict:
    if status not in ("ok", "partial", "unavailable", "denied", "invalid_request"):
        raise ValueError("Unknown result status")
    return {
        "contract_version": CONTRACT_VERSION,
        "tool": tool,
        "department": department,
        "tenant_id": scope.tenant_id,
        "generated_at": now.isoformat(),
        "status": status,
        "answer": jsonable(answer),
        "findings": [jsonable(f) for f in findings],
        "sources": sources,
        "data_quality": {"missing_or_limited": missing},
        "warnings": list(warnings),
        "scope": scope.describe(),
        "read_only": True,
        "actions_executed": False,
    }
