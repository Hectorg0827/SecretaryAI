"""
Action Engine — every AI-proposed action goes through here.
The AI suggests. The engine decides.
Autonomy levels are code-enforced, not prompt-enforced.

V2 addition: Computer Use actions get their own autonomy tier.
Reading/navigating is autonomous; submitting/modifying always requires approval.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from app.actions.audit import log_action


class ActionProhibitedError(Exception):
    pass


class ActionEngine:
    AUTONOMY_RULES: dict[str, str] = {
        # ── AUTONOMOUS — AI executes silently (logged but no user prompt) ──────
        "calculate_health_score": "autonomous",
        "update_inventory_count": "autonomous",
        "run_sync_check": "autonomous",
        "generate_internal_report": "autonomous",
        "score_all_accounts": "autonomous",
        "forecast_demand": "autonomous",
        "detect_anomalies": "autonomous",
        # Computer Use — reading/navigating only
        "cu_read_screen": "autonomous",
        "cu_navigate_app": "autonomous",
        "cu_search_field": "autonomous",

        # ── NOTIFY — AI executes and tells the user ────────────────────────────
        "send_morning_briefing": "notify",
        "send_low_stock_alert": "notify",
        "send_dormant_account_alert": "notify",
        "send_sync_break_alert": "notify",
        "send_anomaly_alert": "notify",
        "send_weekly_report": "notify",

        # ── DRAFT AND WAIT — AI prepares, human must approve ──────────────────
        "draft_customer_email": "draft_and_wait",
        "draft_purchase_order": "draft_and_wait",
        "draft_rep_assignment": "draft_and_wait",
        "generate_external_report": "draft_and_wait",
        # Computer Use — any action that changes data or submits a form
        "cu_submit_form": "draft_and_wait",
        "cu_download_file": "draft_and_wait",
        "cu_data_entry": "draft_and_wait",
        "cu_financial_app_action": "draft_and_wait",

        # ── PROHIBITED — blocked in code, always, no exceptions ───────────────
        "delete_qb_data": "prohibited",
        "modify_financial_records": "prohibited",
        "send_external_email_direct": "prohibited",
        "access_payroll": "prohibited",
        "access_bank_accounts": "prohibited",
        "share_data_externally": "prohibited",
        # Computer Use prohibitions
        "cu_install_app": "prohibited",
        "cu_system_settings": "prohibited",
        "cu_access_password_manager": "prohibited",
        "cu_remote_desktop": "prohibited",
        "cu_terminal_shell": "prohibited",
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

        if action_type == "calculate_health_score":
            account_id = payload.get("account_id", "")
            account_name = payload.get("account_name", "")
            from app.intelligence.account_health import score_account
            from decimal import Decimal
            result = score_account(
                account_id=account_id,
                account_name=account_name,
                last_order_date=payload.get("last_order_date"),
                avg_order_cycle_days=payload.get("avg_order_cycle_days"),
                order_history=payload.get("order_history", []),
                current_balance=Decimal(str(payload.get("current_balance", 0))),
            )
            try:
                self._db.table("accounts").upsert({
                    "company_id": company_id,
                    "qb_id": account_id,
                    "name": account_name,
                    "health_status": result.status,
                    "health_score": result.score,
                    "last_health_check": datetime.now(timezone.utc).isoformat(),
                }, on_conflict="company_id,qb_id").execute()
            except Exception:
                pass
            return {"status": "ok", "health_status": result.status, "health_score": result.score}

        elif action_type == "update_inventory_count":
            item_id = payload.get("item_id", "")
            new_qty = payload.get("quantity", 0)
            try:
                self._db.table("inventory").update({
                    "warehouse_qty": int(new_qty),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }).eq("company_id", company_id).eq("qb_id", item_id).execute()
            except Exception:
                pass
            return {"status": "ok", "item_id": item_id, "new_qty": new_qty}

        handlers = {
            "run_sync_check": lambda p, c: {"status": "ok"},
            "generate_internal_report": generate_internal_report,
            "send_morning_briefing": send_alert_email,
            "send_low_stock_alert": send_alert_email,
            "send_dormant_account_alert": send_alert_email,
            "send_sync_break_alert": send_alert_email,
        }

        handler = handlers.get(action_type)
        if handler:
            import asyncio
            result = handler(payload, company_id)
            if asyncio.iscoroutine(result):
                return await result
            return result
        return {"status": "no_handler", "action_type": action_type}

    async def _create_draft(self, action_type: str, payload: dict, company_id: str) -> dict:
        """Create a draft record for user review via the approval queue."""
        from app.actions.approval_queue import ApprovalQueue
        queue = ApprovalQueue(self._db)
        draft_id = await queue.enqueue(
            company_id=company_id,
            action_type=action_type,
            content=payload,
            created_by="ai",
        )
        return {
            "id": draft_id,
            "action_type": action_type,
            "payload": payload,
            "company_id": company_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
