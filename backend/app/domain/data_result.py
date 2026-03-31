"""
Typed DataResult envelope.

Every data access path (API, browser, file, computer-use) returns a DataResult
so that callers always know:
  - WHERE the data came from (source path)
  - HOW fresh it is (fetched_at)
  - HOW confident we are (confidence 0-100)
  - WHAT the raw structured data is (data)
  - WHETHER the caller should prefer a fresher path (is_stale)

This replaces the raw dict returns scattered across adapters and connectors.

Usage
-----
    result = await access_router.fetch("inventory", company_id=company_id)
    if result.is_ok:
        items = [InventoryItem(**row) for row in result.data]
        log.info("inventory from %s, confidence=%d", result.source, result.confidence)
    else:
        log.warning("inventory fetch failed: %s", result.error)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.domain.contracts import DataPath


# Data older than this many seconds is flagged as stale
DEFAULT_STALE_SECONDS = 300  # 5 minutes


@dataclass
class DataResult:
    """
    Envelope wrapping any data returned through the access router.

    Fields
    ------
    source : DataPath
        The path that produced this result (api, browser, file, computer_use, cache).
    data : list[dict] | dict | None
        The normalised payload. Callers convert to domain types.
    confidence : int
        0–100 estimate of data reliability.
          100 = direct API response (authoritative)
           80 = browser-scraped (reliable but may vary)
           60 = file ingestion (depends on file freshness)
           40 = computer-use OCR (lower accuracy)
            0 = unknown / error
    fetched_at : datetime
        When the data was retrieved (UTC).
    is_ok : bool
        True if the fetch succeeded.
    error : str | None
        Error message if is_ok=False.
    capability : str | None
        The capability key that was requested (e.g. "inventory", "orders").
    correlation_id : str | None
        Propagated from the originating request for tracing.
    route_decision_id : str | None
        ID of the access_router_log row for this decision.
    metadata : dict
        Arbitrary extra context (e.g. QB sync lag, portal response time).
    """
    source: DataPath
    data: list[dict[str, Any]] | dict[str, Any] | None = None
    confidence: int = 0
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_ok: bool = True
    error: str | None = None
    capability: str | None = None
    correlation_id: str | None = None
    route_decision_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def age_seconds(self) -> float:
        """How many seconds ago the data was fetched."""
        now = datetime.now(timezone.utc)
        if self.fetched_at.tzinfo is None:
            return (now - self.fetched_at.replace(tzinfo=timezone.utc)).total_seconds()
        return (now - self.fetched_at).total_seconds()

    @property
    def is_stale(self) -> bool:
        """True when the data is older than DEFAULT_STALE_SECONDS."""
        return self.age_seconds > DEFAULT_STALE_SECONDS

    @property
    def row_count(self) -> int:
        if isinstance(self.data, list):
            return len(self.data)
        if isinstance(self.data, dict):
            return 1
        return 0

    # ── Constructors ─────────────────────────────────────────────────────────

    @classmethod
    def ok(
        cls,
        data: list[dict[str, Any]] | dict[str, Any],
        source: DataPath,
        confidence: int,
        capability: str | None = None,
        correlation_id: str | None = None,
        route_decision_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DataResult":
        """Convenience constructor for successful results."""
        return cls(
            source=source,
            data=data,
            confidence=confidence,
            is_ok=True,
            capability=capability,
            correlation_id=correlation_id,
            route_decision_id=route_decision_id,
            metadata=metadata or {},
        )

    @classmethod
    def from_error(
        cls,
        error: str,
        source: DataPath,
        capability: str | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DataResult":
        """Convenience constructor for failed results."""
        return cls(
            source=source,
            data=None,
            confidence=0,
            is_ok=False,
            error=error,
            capability=capability,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable representation (for logging / API responses)."""
        return {
            "source": self.source.value,
            "confidence": self.confidence,
            "fetched_at": self.fetched_at.isoformat(),
            "age_seconds": round(self.age_seconds, 1),
            "is_ok": self.is_ok,
            "is_stale": self.is_stale,
            "row_count": self.row_count,
            "error": self.error,
            "capability": self.capability,
            "correlation_id": self.correlation_id,
            "route_decision_id": self.route_decision_id,
            "metadata": self.metadata,
        }


# ─── Confidence constants ─────────────────────────────────────────────────────

class Confidence:
    """Standard confidence scores per data path."""
    API          = 100   # Direct API call — authoritative
    CACHE_FRESH  = 90    # Cached API result < 5 min old
    BROWSER      = 75    # Playwright browser scrape
    FILE_FRESH   = 65    # File uploaded today
    FILE_STALE   = 45    # File older than 24 h
    COMPUTER_USE = 35    # Computer-use OCR — lower accuracy (legacy alias)
    CU_FRESH     = 55    # CU cached result < 15 min old
    CU_STALE     = 25    # CU cached result 15 min–1 hour old
    UNKNOWN      = 0
