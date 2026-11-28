"""Proactive alert section for Lola's morning briefing.

Takes the inventory alerts from the dashboard snapshot, removes duplicates
(the same product flagged twice), and splits what is left into:

* ``new``      - not mentioned in a recent briefing, so call it out;
* ``ongoing``  - already reported and still unresolved.

Nothing is ever dropped silently. An alert that was already reported is still
listed under "still open", so a critical stock-out cannot vanish from the
briefing just because it was mentioned yesterday. The only hard cap is the
``max_alerts`` length limit, and the summary states how many were left out.

History of what was reported lives in ``report_snapshots`` under
``BRIEFING_ALERT_HISTORY_TYPE`` (one row per company). If that history cannot
be read we treat every alert as new: the safe failure is "too loud", never
"too quiet".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from app.intelligence.anomaly_detection import (
    Anomaly,
    deduplicate_and_prioritize_anomalies,
)

log = logging.getLogger(__name__)

BRIEFING_ALERT_HISTORY_TYPE = "briefing_alert_history"

# A daily briefing runs roughly every 24h. If history is older than this,
# a briefing was missed, so re-announce instead of assuming people saw it.
BRIEFING_ALERT_COOLDOWN = timedelta(hours=36)
BRIEFING_MAX_ALERTS = 10
_MAX_NAME_CHARS = 80

# stock_status from the dashboard snapshot -> alert severity
_STATUS_SEVERITY = {
    "out_of_stock": "critical",
    "critical": "high",
    "low": "medium",
}


def _clean_text(value, limit: int = _MAX_NAME_CHARS) -> str:
    """Single-line, length-capped text. Product names come from the ERP and
    are fed to the model, so strip line breaks and control characters."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    text = " ".join(text.split())
    return text[:limit]


def inventory_alerts_to_anomalies(inventory_alerts, *, today) -> list[Anomaly]:
    """Convert dashboard ``inventory_alerts`` rows into ``Anomaly`` objects."""
    anomalies: list[Anomaly] = []
    for row in inventory_alerts or []:
        if not isinstance(row, dict):
            continue
        status = str(row.get("stock_status") or "").strip().lower()
        severity = _STATUS_SEVERITY.get(status)
        if severity is None:
            continue
        name = _clean_text(row.get("product_name"))
        item_id = _clean_text(row.get("item_id"), limit=40)
        if not name and not item_id:
            continue
        # Include the item id so two different products that share a display
        # name are never merged into one alert.
        entity = f"{name or 'Unnamed item'} (#{item_id})" if item_id else name
        weeks = row.get("weeks_remaining")
        try:
            weeks_value = float(weeks) if weeks is not None else 0.0
        except (TypeError, ValueError):
            weeks_value = 0.0
        if status == "out_of_stock":
            description = "Out of stock"
        elif weeks is not None:
            description = f"About {weeks_value:.1f} weeks of supply left"
        else:
            description = "Running low"
        anomalies.append(Anomaly(
            anomaly_type=f"inventory_{status}",
            entity_name=entity,
            description=description,
            severity=severity,
            value=weeks_value,
            expected_range=(2.0, float("inf")),
            detected_at=today,
        ))
    return anomalies


def _parse_history(payload, *, now: datetime) -> dict[str, datetime]:
    """Read ``{fingerprint: iso timestamp}`` from a stored snapshot payload.
    Bad or future-dated entries are ignored (which makes that alert "new")."""
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("reported_at")
    if not isinstance(raw, dict):
        return {}
    history: dict[str, datetime] = {}
    for fp, ts in raw.items():
        if not isinstance(fp, str) or not isinstance(ts, str):
            continue
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if parsed.tzinfo is None or parsed > now:
            continue
        history[fp] = parsed
    return history


def load_alert_history(db, company_id: str, *, now: datetime) -> dict[str, datetime]:
    """Previously reported fingerprints for this company. Never raises."""
    from app.scheduler.dashboard_snapshot import read_snapshot

    try:
        row = read_snapshot(db, company_id, BRIEFING_ALERT_HISTORY_TYPE)
    except Exception:
        log.warning("Briefing alert history unavailable; treating all alerts as new")
        return {}
    if not row:
        return {}
    return _parse_history(row.get("payload"), now=now)


def build_briefing_alerts(
    inventory_alerts,
    *,
    company_id: str,
    now: datetime,
    history: dict[str, datetime] | None = None,
) -> dict:
    """Return the new/ongoing split plus the history to save afterwards."""
    anomalies = inventory_alerts_to_anomalies(inventory_alerts, today=now.date())

    # Pass 1: everything currently active, duplicates merged.
    current = deduplicate_and_prioritize_anomalies(
        anomalies,
        tenant_id=company_id,
        now=now,
        recent_fingerprints=None,
        cooldown=BRIEFING_ALERT_COOLDOWN,
        max_alerts=max(len(anomalies), 1),
    )
    # Pass 2: what is new since a recent briefing.
    fresh = deduplicate_and_prioritize_anomalies(
        anomalies,
        tenant_id=company_id,
        now=now,
        recent_fingerprints=history or {},
        cooldown=BRIEFING_ALERT_COOLDOWN,
        max_alerts=max(len(anomalies), 1),
    )
    new_fps = set(fresh["fingerprints"])
    new_alerts = [a for a in current["active_alerts"] if a["fingerprint"] in new_fps]
    ongoing = [a for a in current["active_alerts"] if a["fingerprint"] not in new_fps]

    # Length cap: new alerts take priority over ongoing ones; both lists are
    # already sorted most-severe first.
    shown_new = new_alerts[:BRIEFING_MAX_ALERTS]
    room = max(0, BRIEFING_MAX_ALERTS - len(shown_new))
    shown_ongoing = ongoing[:room]
    omitted = (len(new_alerts) - len(shown_new)) + (len(ongoing) - len(shown_ongoing))

    # Keep only alerts that are still active, so a problem that is fixed and
    # later comes back counts as new again. Ongoing alerts keep their earlier
    # timestamp refreshed to "now" because they were reported again today.
    next_history = {a["fingerprint"]: now.isoformat() for a in current["active_alerts"]}

    return {
        "new": shown_new,
        "ongoing": shown_ongoing,
        "omitted_count": omitted,
        "duplicates_merged": current["deduplicated_count"],
        "history_payload": {"reported_at": next_history},
    }


def alerts_to_text(result: dict) -> str:
    """Plain-text section appended to the briefing data for the model."""
    new = result.get("new") or []
    ongoing = result.get("ongoing") or []
    if not new and not ongoing:
        return "Proactive alerts: none active."
    lines = ["Proactive alerts (duplicates merged, most severe first):"]
    if new:
        lines.append("NEW since the last briefing:")
        lines += [f"- [{a['severity']}] {a['entity_name']}: {a['description']}" for a in new]
    if ongoing:
        lines.append("STILL OPEN (already reported, not yet resolved):")
        lines += [f"- [{a['severity']}] {a['entity_name']}: {a['description']}" for a in ongoing]
    if result.get("omitted_count"):
        lines.append(f"(+{result['omitted_count']} more alerts not listed)")
    return "\n".join(lines)


def save_alert_history(db, company_id: str, result: dict) -> None:
    """Remember what this briefing reported. Never raises: a failed save only
    means tomorrow's briefing may repeat today's alerts as new."""
    from app.scheduler.dashboard_snapshot import store_snapshot

    try:
        store_snapshot(
            db, company_id, BRIEFING_ALERT_HISTORY_TYPE,
            result["history_payload"], generated_by="morning_briefing",
        )
    except Exception:
        log.warning("Could not save briefing alert history for company %s", company_id)
