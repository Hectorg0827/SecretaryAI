"""Gate 2: Railway cron cannot activate global Celery or cross-tenant writes."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tasks import staging_snapshot_job as job
from app.scheduler.dashboard_snapshot import SnapshotSourceUnavailable


def safe_settings(**kwargs):
    values = dict(
        secretaryai_deployment_env="staging",
        secretaryai_expected_supabase_project_ref=job.STAGING_SUPABASE_REF,
        intuit_environment="sandbox",
        computer_use_enabled=False,
        secretaryai_push_delivery_enabled=False,
        secretaryai_email_delivery_mode="disabled",
        secretaryai_live_email_delivery_approved=False,
        secretaryai_staging_email_pilot_enabled=False,
        secretaryai_invite_delivery_enabled=False,
    )
    values.update(kwargs)
    return SimpleNamespace(**values)


def safe_env(**kwargs):
    values = dict(
        RAILWAY_PROJECT_ID=job.STAGING_PROJECT_ID,
        RAILWAY_ENVIRONMENT_ID=job.STAGING_ENVIRONMENT_ID,
        RAILWAY_ENVIRONMENT_NAME="staging",
        SECRETARYAI_READONLY_REFRESH_COMPANY_ID=job.STAGING_COMPANY_ID,
    )
    values.update(kwargs)
    return values


@contextmanager
def lock(granted):
    yield granted


@pytest.mark.parametrize("settings_overrides,env_overrides", [
    ({"secretaryai_deployment_env": "production"}, {}),
    ({"secretaryai_expected_supabase_project_ref": "wrong"}, {}),
    ({"intuit_environment": "production"}, {}),
    ({"computer_use_enabled": True}, {}),
    ({"secretaryai_push_delivery_enabled": True}, {}),
    ({"secretaryai_email_delivery_mode": "live"}, {}),
    ({"secretaryai_live_email_delivery_approved": True}, {}),
    ({"secretaryai_staging_email_pilot_enabled": True}, {}),
    ({"secretaryai_invite_delivery_enabled": True}, {}),
    ({}, {"RAILWAY_PROJECT_ID": "other"}),
    ({}, {"RAILWAY_ENVIRONMENT_ID": "other"}),
    ({}, {"RAILWAY_ENVIRONMENT_NAME": "production"}),
    ({}, {"SECRETARYAI_READONLY_REFRESH_COMPANY_ID": ""}),
    ({}, {"SECRETARYAI_READONLY_REFRESH_COMPANY_ID": "00000000-0000-0000-0000-000000000000"}),
])
def test_any_unsafe_config_fails_before_database_access(settings_overrides, env_overrides):
    db_factory = MagicMock()
    with pytest.raises(RuntimeError):
        job.run_once(
            settings=safe_settings(**settings_overrides),
            environ=safe_env(**env_overrides),
            db_factory=db_factory,
        )
    db_factory.assert_not_called()


def test_single_tenant_refresh_cannot_enqueue_outbound_tasks():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": job.STAGING_COMPANY_ID, "qb_type": "online", "qb_connection_status": "connected"}
    ]
    adapter = MagicMock()
    with patch.object(job, "refresh_company_snapshot", return_value={
        "status": "refreshed", "source_read_status": "completed", "source_quality": "partial",
    }) as refresh:
        outcome = job.run_once(
            settings=safe_settings(), environ=safe_env(),
            db_factory=lambda: db, adapter_factory=lambda _: adapter,
            lock=lambda *args, **kwargs: lock(True),
        )
    assert outcome == {
        "status": "refreshed", "source_read_status": "completed", "source_quality": "partial",
    }
    db.table.assert_called_once_with("companies")
    eq_calls = db.table.return_value.select.return_value.eq.call_args_list
    assert eq_calls[0].args == ("id", job.STAGING_COMPANY_ID)
    assert db.table.return_value.select.return_value.eq.return_value.eq.call_args.args == (
        "qb_connection_status", "connected",
    )
    refresh.assert_called_once_with(
        job.STAGING_COMPANY_ID, db=db,
        companies=[{"id": job.STAGING_COMPANY_ID, "qb_type": "online", "qb_connection_status": "connected"}],
        adapter_factory=refresh.call_args.kwargs["adapter_factory"],
        generated_by="staging_readonly_cron",
        include_finance_comparison=True,
        include_purchasing_coverage=True,
        include_finance_document_coverage=True,
        stage_hook=refresh.call_args.kwargs["stage_hook"],
    )


def test_disconnected_or_wrong_company_fails_without_refresh():
    for rows in ([], [{"id": "other", "qb_type": "online"}],
                 [{"id": job.STAGING_COMPANY_ID, "qb_type": "desktop"}]):
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = rows
        with patch.object(job, "refresh_company_snapshot") as refresh:
            with pytest.raises(RuntimeError, match="Approved company"):
                job.run_once(
                    settings=safe_settings(), environ=safe_env(), db_factory=lambda: db,
                    lock=lambda *args, **kwargs: lock(True),
                )
            refresh.assert_not_called()


def test_lock_collision_skips_before_database_access():
    db_factory = MagicMock()
    outcome = job.run_once(
        settings=safe_settings(), environ=safe_env(), db_factory=db_factory,
        lock=lambda *args, **kwargs: lock(False),
    )
    assert outcome == {"status": "skipped", "reason": "refresh_already_running"}
    db_factory.assert_not_called()


def test_main_never_logs_provider_exception_details(caplog):
    with patch.object(job, "run_once", side_effect=RuntimeError("SENSITIVE_CANARY")):
        assert job.main() == 1
    assert "SENSITIVE_CANARY" not in caplog.text
    assert "Staging read-only snapshot refresh failed" in caplog.text


def test_refresh_source_failure_records_precise_redacted_stage():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
        {"id": job.STAGING_COMPANY_ID, "qb_type": "online", "qb_connection_status": "connected"}
    ]

    def failing_refresh(*args, stage_hook, **kwargs):
        stage_hook("read_required_dashboard_sources")
        raise SnapshotSourceUnavailable("inventory") from RuntimeError(
            "SENSITIVE_PROVIDER_URL_AND_ACCOUNT"
        )

    with patch.object(job, "refresh_company_snapshot", side_effect=failing_refresh):
        with pytest.raises(SnapshotSourceUnavailable):
            job.run_once(
                settings=safe_settings(), environ=safe_env(),
                db_factory=lambda: db, lock=lambda *a, **k: lock(True),
            )
    assert job._FAILURE_STAGE.get() == "read_required_dashboard_sources"
    assert job._safe_failure_code(SnapshotSourceUnavailable("inventory")) == "required_source_inventory"


def test_failure_classification_only_emits_predefined_safe_codes(caplog):
    with patch.object(job, "run_once", side_effect=RuntimeError("SENSITIVE_API_TOKEN_AND_URL")):
        assert job.main() == 1
    assert "code=dependency_or_configuration_failed" in caplog.text
    assert "SENSITIVE_API_TOKEN_AND_URL" not in caplog.text
    assert job._safe_failure_code(SnapshotSourceUnavailable("UNTRUSTED_INPUT")) == "required_source_unavailable"
