"""
Audit logger — every action taken by the AI or system is recorded.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


async def log_action(
    db,
    company_id: str,
    actor: str,
    action_type: str,
    autonomy_level: str,
    payload: dict[str, Any],
    status: str,
    approved_by: Optional[str] = None,
) -> str:
    """
    Insert an audit record. Returns the log entry ID.
    db is a Supabase client or SQLAlchemy session.
    """
    entry_id = str(uuid.uuid4())
    record = {
        "id": entry_id,
        "company_id": company_id,
        "actor": actor,
        "action_type": action_type,
        "autonomy_level": autonomy_level,
        "description": f"{actor} → {action_type}",
        "data_involved": payload,
        "status": status,
        "approved_by": approved_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Supabase client pattern
    if hasattr(db, "table"):
        db.table("action_log").insert(record).execute()
    # SQLAlchemy pattern (fallback)
    else:
        db.execute(
            "INSERT INTO action_log VALUES (:id, :company_id, :actor, :action_type, "
            ":autonomy_level, :description, :data_involved, :status, :approved_by, :created_at)",
            record,
        )

    return entry_id
