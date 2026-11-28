"""
Anomaly detection — flags unusual patterns in sales, inventory, and accounts.
Uses simple statistical methods (z-score, rolling average deviation) to avoid
false positives while catching real issues.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import math
import statistics
from typing import Mapping, Optional, Sequence


@dataclass
class Anomaly:
    anomaly_type: str
    entity_name: str
    description: str
    severity: str       # "low" | "medium" | "high"
    value: float
    expected_range: tuple[float, float]
    detected_at: date


def detect_order_anomalies(
    invoices: list[dict],
    lookback_days: int = 90,
    today: Optional[date] = None,
) -> list[Anomaly]:
    """
    Detect unusual order patterns:
    - Unusually large or small orders for an account
    - Accounts that placed double their normal order
    - Accounts with sudden order gaps
    """
    today = today or date.today()
    cutoff = today - timedelta(days=lookback_days)
    anomalies: list[Anomaly] = []

    # Group by account
    by_account: dict[str, list[dict]] = {}
    for inv in invoices:
        acct = inv.get("customer_name", "Unknown")
        by_account.setdefault(acct, []).append(inv)

    for acct_name, acct_invoices in by_account.items():
        # Flag only the orders the baseline was computed from: an order outside
        # the window (or with an unreadable amount) is not compared at all,
        # and a missing amount is never treated as a $0 order.
        amounts = [
            amount
            for inv in acct_invoices
            if _parse_date(inv.get("order_date", "")) >= cutoff
            and (amount := _parse_amount(inv.get("total_amount"))) is not None
        ]

        if len(amounts) < 4:
            continue  # Not enough data

        mean = statistics.mean(amounts)
        stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0

        for amount in amounts:
            if stdev == 0:
                continue

            z_score = (amount - mean) / stdev
            if abs(z_score) > 2.5:
                direction = "high" if z_score > 0 else "low"
                severity = "high" if abs(z_score) > 3.0 else "medium"
                anomalies.append(Anomaly(
                    anomaly_type=f"unusual_order_amount_{direction}",
                    entity_name=acct_name,
                    description=(
                        f"{acct_name} placed an unusually {direction} order: "
                        f"${amount:,.0f} (avg: ${mean:,.0f} ± ${stdev:,.0f})"
                    ),
                    severity=severity,
                    value=amount,
                    expected_range=(
                        round(mean - 2 * stdev, 2),
                        round(mean + 2 * stdev, 2),
                    ),
                    detected_at=today,
                ))

    return anomalies


def detect_inventory_anomalies(
    inventory: list[dict],
    today: Optional[date] = None,
) -> list[Anomaly]:
    """
    Detect inventory anomalies:
    - Product quantity dropped to zero unexpectedly
    - Sell rate spike (possible data error or sudden demand)
    """
    today = today or date.today()
    anomalies: list[Anomaly] = []

    for item in inventory:
        raw_qty = item.get("total_qty") if item.get("total_qty") is not None else item.get("quantity_on_hand")
        qty = _parse_amount(raw_qty)
        if qty is None:
            continue
        sell_rate = _parse_amount(item.get("weekly_sell_rate"))
        name = str(item.get("product_name") or "Unknown")

        # Negative quantity (data error)
        if qty < 0:
            anomalies.append(Anomaly(
                anomaly_type="negative_inventory",
                entity_name=name,
                description=f"{name} has NEGATIVE inventory ({qty:.0f} units). Possible data error.",
                severity="high",
                value=qty,
                expected_range=(0.0, float("inf")),
                detected_at=today,
            ))

        # Zero quantity with high sell rate (should have been caught earlier)
        if qty == 0 and sell_rate is not None and sell_rate > 0:
            anomalies.append(Anomaly(
                anomaly_type="zero_stock_active_item",
                entity_name=name,
                description=f"{name} is out of stock but still selling ({sell_rate:.1f}/wk sell rate).",
                severity="high",
                value=0.0,
                expected_range=(sell_rate * 4, float("inf")),  # At least 4 weeks stock
                detected_at=today,
            ))

    return anomalies


_SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def alert_fingerprint(anomaly: Anomaly, *, tenant_id: str) -> str:
    """Compute a tenant-scoped deterministic SHA-256 fingerprint for deduplication."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise PermissionError("Tenant context required for alert fingerprinting")
    normalized_entity = " ".join(str(anomaly.entity_name).strip().lower().split())
    normalized_type = str(anomaly.anomaly_type).strip().lower()
    raw = f"{tenant_id.strip()}|{normalized_type}|{normalized_entity}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deduplicate_and_prioritize_anomalies(
    anomalies: Sequence[Anomaly],
    *,
    tenant_id: str,
    now: datetime,
    recent_fingerprints: Mapping[str, datetime] | None = None,
    cooldown: timedelta = timedelta(hours=24),
    max_alerts: int = 10,
) -> dict:
    """Deduplicate proactive alerts, apply cooldown suppression, and rank cleanly."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise PermissionError("Tenant context required for alert deduplication")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(cooldown, timedelta) or cooldown <= timedelta(0):
        raise ValueError("cooldown must be positive")
    if not isinstance(max_alerts, int) or isinstance(max_alerts, bool) or max_alerts < 1:
        raise ValueError("max_alerts must be a positive integer")

    best_by_fp: dict[str, Anomaly] = {}
    deduplicated_count = 0

    for item in anomalies:
        fp = alert_fingerprint(item, tenant_id=tenant_id)
        existing = best_by_fp.get(fp)
        if existing is None:
            best_by_fp[fp] = item
            continue
        deduplicated_count += 1
        item_key = (_SEVERITY_RANK.get(item.severity, 99), -abs(item.value), item.description)
        cur_key = (_SEVERITY_RANK.get(existing.severity, 99), -abs(existing.value), existing.description)
        if item_key < cur_key:
            best_by_fp[fp] = item

    suppressed_count = 0
    eligible: list[tuple[str, Anomaly]] = []
    history = recent_fingerprints or {}

    for fp, item in best_by_fp.items():
        last_sent = history.get(fp)
        if last_sent is not None:
            if not isinstance(last_sent, datetime) or last_sent.tzinfo is None or last_sent.utcoffset() is None:
                raise ValueError("recent_fingerprints timestamps must be timezone-aware")
            elapsed = now - last_sent
            if elapsed < timedelta(0):
                raise ValueError("recent_fingerprints timestamp is in the future")
            if elapsed < cooldown:
                suppressed_count += 1
                continue
        eligible.append((fp, item))

    eligible.sort(
        key=lambda pair: (
            _SEVERITY_RANK.get(pair[1].severity, 99),
            pair[1].anomaly_type,
            pair[1].entity_name.casefold(),
        )
    )

    selected = eligible[:max_alerts]
    truncated_count = max(0, len(eligible) - len(selected))

    return {
        "tenant_id": tenant_id,
        "evaluated_at": now.isoformat(),
        "active_alerts": [
            {
                "fingerprint": fp,
                "anomaly_type": item.anomaly_type,
                "entity_name": item.entity_name,
                "severity": item.severity,
                "description": item.description,
                "value": item.value,
                "detected_at": item.detected_at.isoformat(),
            }
            for fp, item in selected
        ],
        "fingerprints": [fp for fp, _ in selected],
        "deduplicated_count": deduplicated_count,
        "suppressed_count": suppressed_count,
        "truncated_count": truncated_count,
    }


def _parse_amount(value) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except (ValueError, TypeError):
        return None
    return amount if math.isfinite(amount) else None


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return date(2000, 1, 1)
