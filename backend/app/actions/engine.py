"""
Action Engine — every AI-proposed action goes through here.
The AI suggests. The engine decides.
Autonomy levels are code-enforced, not prompt-enforced.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from app.actions.audit import log_action


class ActionProhibitedError(Exception):
    pass


class ActionEngine:
    AUTONOMY_RULES: dict[str, str] = {
        # AUTONOMOUS — AI executes silently
        "calculate_health_score": "autonomous",
        "update_inventory_count": "autonomous",
        "run_sync_check": "autonomous",
        "generate_internal_report": "autonomous",
        # NOTIFY — AI executes and informs the user
        "send_morning_briefing": "notify",
        "send_low_stock_alert": "notify",
        "send_dormant_account_alert": "notify",
        "send_sync_break_alert": "notify",
        # DRAFT AND WAIT — AI prepares, human approves before anything is sent
        "draft_customer_email": "draft_and_wait",
        "draft_purchase_order": "draft_and_wait",
        "draft_rep_assignment": "draft_and_wait",
        "generate_external_report": "draft_and_wait",
        # PROHIBITED — blocked in code, always, no exceptions
        "delete_qb_data": "prohibited",
        "modify_financial_records": "prohibited",
        "send_external_email_direct": "prohibited",
        "access_payroll": "prohibited",
        "access_bank_accounts": "prohibited",
        "share_data_externally": "prohibited",
    }

    def __init__(self, db_session, notifier=None):
        self._db = db_session
        self._notifier = notifier

    async def process(
        self,
        action_type: str,
        payload: dict[str, Any],
        company_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        level = self.AUTONOMY_RULES.get(action_type, "prohibited")

        await log_action(
            self._db,
            company_id=company_id,
            actor=user_id,
            action_type=action_type,
            autonomy_level=level,
            payload=payload,
            status="pending",
        )

        if level == "prohibited":
            await log_action(
                self._db,
                company_id=company_id,
                actor="system",
                action_type=action_type,
                autonomy_level=level,
                payload=payload,
                status="blocked",
            )
            raise ActionProhibitedError(
                f"Action '{action_type}' is permanently blocked by SecretaryAI policy."
            )

        if level == "autonomous":
            result = await self._execute(action_type, payload, company_id)
            await log_action(
                self._db,
                company_id=company_id,
                actor="ai",
                action_type=action_type,
                autonomy_level=level,
                payload=payload,
                status="executed",
            )
            return result

        if level == "notify":
            result = await self._execute(action_type, payload, company_id)
            if self._notifier:
                await self._notifier.notify(user_id, action_type, result)
            await log_action(
                self._db,
                company_id=company_id,
                actor="ai",
                action_type=action_type,
                autonomy_level=level,
                payload=payload,
                status="executed",
            )
            return result

        if level == "draft_and_wait":
            draft = await self._create_draft(action_type, payload, company_id)
            await log_action(
                self._db,
                company_id=company_id,
                actor="ai",
                action_type=action_type,
                autonomy_level=level,
                payload=payload,
                status="pending_approval",
            )
            return {"status": "pending_approval", "draft_id": draft["id"]}

        raise ValueError(f"Unknown autonomy level: {level}")

    async def _execute(self, action_type: str, payload: dict, company_id: str) -> dict:
        """Dispatch to the appropriate action handler."""
        from app.actions.email_actions import send_alert_email
        from app.actions.report_actions import generate_internal_report

        handlers = {
            "calculate_health_score": lambda p, c: {"status": "ok"},
            "update_inventory_count": lambda p, c: {"status": "ok"},
            "run_sync_check": lambda p, c: {"status": "ok"},
            "generate_internal_report": generate_internal_report,
            "send_morning_briefing": send_alert_email,
            "send_low_stock_alert": send_alert_email,
            "send_dormant_account_alert": send_alert_email,
            "send_sync_break_alert": send_alert_email,
        }

        handler = handlers.get(action_type)
        if handler:
            return await handler(payload, company_id)
        return {"status": "no_handler", "action_type": action_type}

    async def _create_draft(self, action_type: str, payload: dict, company_id: str) -> dict:
        """Create a draft record for user review."""
        draft_id = str(uuid.uuid4())
        # Store draft in DB for user to review
        return {
            "id": draft_id,
            "action_type": action_type,
            "payload": payload,
            "company_id": company_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
