"""
Approval Queue — manages DRAFT_AND_WAIT actions waiting for human review.
The AI can create drafts; only humans can approve and execute them.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional


class ApprovalQueue:
    """
    Stores pending draft actions and manages their approval lifecycle.
    Backed by the `drafts` table in Supabase.
    """

    def __init__(self, db):
        self._db = db  # Supabase client

    async def enqueue(
        self,
        company_id: str,
        action_type: str,
        content: dict,
        created_by: str = "ai",
        workflow_run_id: Optional[str] = None,
        expires_hours: int = 48,
    ) -> str:
        """Add a draft to the approval queue. Returns the draft ID."""
        from datetime import timedelta
        draft_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record = {
            "id": draft_id,
            "company_id": company_id,
            "created_by": created_by,
            "action_type": action_type,
            "content": content,
            "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=expires_hours)).isoformat(),
        }
        if workflow_run_id:
            record["workflow_run_id"] = workflow_run_id

        if hasattr(self._db, "table"):
            self._db.table("drafts").insert(record).execute()

        return draft_id

    async def get_pending(self, company_id: str) -> list[dict]:
        """Get all pending drafts for a company."""
        if hasattr(self._db, "table"):
            result = (
                self._db.table("drafts")
                .select("*")
                .eq("company_id", company_id)
                .eq("status", "pending")
                .order("created_at", desc=True)
                .execute()
            )
            return result.data or []
        return []

    async def approve(self, draft_id: str, reviewed_by: str) -> dict:
        """Mark a draft as approved. Caller is responsible for executing the action."""
        if hasattr(self._db, "table"):
            self._db.table("drafts").update({
                "status": "approved",
                "reviewed_by": reviewed_by,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", draft_id).execute()
        return {"draft_id": draft_id, "status": "approved"}

    async def reject(self, draft_id: str, reviewed_by: str, reason: Optional[str] = None) -> dict:
        """Mark a draft as rejected."""
        update = {
            "status": "rejected",
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        if reason:
            update["rejection_reason"] = reason

        if hasattr(self._db, "table"):
            self._db.table("drafts").update(update).eq("id", draft_id).execute()
        return {"draft_id": draft_id, "status": "rejected"}

    async def edit_and_approve(self, draft_id: str, edited_content: dict, reviewed_by: str) -> dict:
        """Apply edits to a draft, then approve it."""
        if hasattr(self._db, "table"):
            self._db.table("drafts").update({
                "content": edited_content,
                "status": "edited_and_approved",
                "reviewed_by": reviewed_by,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", draft_id).execute()
        return {"draft_id": draft_id, "status": "edited_and_approved", "content": edited_content}

    def expire_old_drafts(self, company_id: str) -> int:
        """
        Archive pending drafts that have passed their expires_at timestamp.
        Returns the number of drafts expired.
        Called nightly by the overnight task.
        """
        if not hasattr(self._db, "table"):
            return 0
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            result = (
                self._db.table("drafts")
                .update({"status": "expired"})
                .eq("company_id", company_id)
                .eq("status", "pending")
                .lt("expires_at", now_iso)
                .execute()
            )
            return len(result.data) if result.data else 0
        except Exception:
            return 0
