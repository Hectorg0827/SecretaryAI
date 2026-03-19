"""Report generation actions."""
from datetime import datetime, timezone


async def generate_internal_report(payload: dict, company_id: str) -> dict:
    """Generate an internal report (AUTONOMOUS level)."""
    report_type = payload.get("report_type", "summary")
    return {
        "status": "generated",
        "report_type": report_type,
        "company_id": company_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": payload.get("data", {}),
    }
