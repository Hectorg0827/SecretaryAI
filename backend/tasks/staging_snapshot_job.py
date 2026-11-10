"""Single-tenant, staging-only snapshot refresh for an isolated Railway cron service.

This is intentionally *not* the Celery beat entrypoint: the normal beat schedule
contains outbound notifications and financial workflows. This entrypoint only
reads sandbox accounting data and persists the company's dashboard snapshot.
It never dispatches Celery tasks, runs the AI agent, or sends notifications.
"""
from __future__ import annotations

import json
import logging
import os
from uuid import UUID

from app.config import get_settings
from app.utils.log_safety import configure_safe_transport_logging
from tasks.base import build_adapter, get_supabase, task_lock
from tasks.dashboard_refresh import refresh_company_snapshot

log = logging.getLogger(__name__)

# This pilot is deliberately bound to the existing SecretaryAI staging stack.
STAGING_PROJECT_ID = "77513c77-44df-485e-a927-d57d6a80b2c1"
STAGING_ENVIRONMENT_ID = "f9dc2025-cd5b-4df2-95bc-c3c54a3c3e36"
STAGING_COMPANY_ID = "89399925-ea7d-43f3-91cd-95f9a5636b73"
STAGING_SUPABASE_REF = "xerktvdgjvjozvckjyfp"


def validated_company_id(settings, environ) -> str:
    """Fail closed before network access if any staging safety gate is missing."""
    if settings.secretaryai_deployment_env.strip().lower() != "staging":
        raise RuntimeError("Snapshot cron requires isolated staging settings")
    if settings.secretaryai_expected_supabase_project_ref != STAGING_SUPABASE_REF:
        raise RuntimeError("Snapshot cron is not using the approved Supabase project")
    if (environ.get("RAILWAY_PROJECT_ID") != STAGING_PROJECT_ID
            or environ.get("RAILWAY_ENVIRONMENT_ID") != STAGING_ENVIRONMENT_ID
            or environ.get("RAILWAY_ENVIRONMENT_NAME") != "staging"):
        raise RuntimeError("Snapshot cron is not running in the approved staging environment")

    company_id = environ.get("SECRETARYAI_READONLY_REFRESH_COMPANY_ID", "")
    try:
        if str(UUID(company_id)) != STAGING_COMPANY_ID:
            raise ValueError("Unapproved company")
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("Snapshot cron company is not allowlisted") from exc

    if (settings.intuit_environment != "sandbox"
            or settings.computer_use_enabled
            or settings.secretaryai_push_delivery_enabled
            or settings.secretaryai_email_delivery_mode != "disabled"
            or settings.secretaryai_live_email_delivery_approved
            or settings.secretaryai_staging_email_pilot_enabled
            or settings.secretaryai_invite_delivery_enabled):
        raise RuntimeError("Snapshot cron requires all outbound actions disabled")
    return company_id


def run_once(*, settings=None, environ=None, db_factory=None,
             adapter_factory=None, lock=None) -> dict:
    """Refresh a single approved QBO sandbox tenant or fail without a write."""
    settings = settings if settings is not None else get_settings()
    environ = environ if environ is not None else os.environ
    company_id = validated_company_id(settings, environ)
    db_factory = db_factory or get_supabase
    adapter_factory = adapter_factory or build_adapter
    lock = lock or task_lock

    with lock(f"staging_readonly_snapshot_{company_id}", ttl_seconds=3300) as acquired:
        if not acquired:
            return {"status": "skipped", "reason": "refresh_already_running"}

        db = db_factory()
        result = (
            db.table("companies").select("*")
            .eq("id", company_id)
            .eq("qb_connection_status", "connected")
            .execute()
        )
        companies = result.data or []
        if (len(companies) != 1 or companies[0].get("id") != company_id
                or companies[0].get("qb_type") != "online"):
            raise RuntimeError("Approved company does not have one connected sandbox source")

        refreshed = refresh_company_snapshot(
            company_id, db=db, companies=companies,
            adapter_factory=adapter_factory,
            generated_by="staging_readonly_cron",
            include_finance_comparison=True,
            include_purchasing_coverage=True,
            include_finance_document_coverage=True,
        )
        # Never log fetched customer, financial, inventory, or credential values.
        return {
            "status": refreshed["status"],
            "source_read_status": refreshed["source_read_status"],
            "source_quality": refreshed["source_quality"],
        }


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    configure_safe_transport_logging()
    try:
        outcome = run_once()
    except Exception:
        # Keep exception chains (e.g. provider requests) out of persisted logs.
        log.error("Staging read-only snapshot refresh failed")
        return 1
    print(json.dumps(outcome, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
