"""Stage 12 SQL staging recovery source security and migration integrity checks.

These are static guards in addition to real disposable PostgreSQL CI tests.
"""
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase/migrations/20261115233000_lola_gmail_recovery_epochs_staging.sql"
)


def migration():
    return MIGRATION.read_text(encoding="utf-8")


def test_recovery_migration_is_single_well_terminated_transaction():
    s=migration()
    assert s.count("COMMIT;")==1
    assert s.strip().endswith("COMMIT;")
    assert s.count("CREATE FUNCTION public.lola_recover_expired_gmail_replay(")==1
    assert s.count("CREATE FUNCTION public.lola_bind_replay_event_recovery_epoch()")==1
    assert s.count("CREATE TRIGGER lola_provider_replay_event_epoch_gate")==1
    assert s.count("$body$;")==2
    assert "OR p_cursor_ciphertext !~ '^v1:[A-Za-z0-9+/=]+$'" in s
    assert "OR length(p_cursor_ciphertext) NOT BETWEEN 40 AND 8192" in s


def test_recovery_rpc_requires_role_consent_locked_cas_and_no_event_deletion():
    s=migration()
    assert "SECURITY INVOKER" in s
    assert "current_user IS DISTINCT FROM 'service_role'" in s
    assert "current_setting('request.jwt.claim.role', TRUE)" in s
    assert "p_expected_cursor_sha256" in s
    assert "p_expected_generation" in s
    assert "FROM public.lola_provider_replay_checkpoints" in s
    assert "FOR UPDATE;" in s
    assert "baseline_only=TRUE" in s
    assert "recovery_epoch=v_new_epoch" in s
    assert "reason TEXT NOT NULL CHECK (reason = 'gmail_history_404')" in s
    assert "DELETE FROM public.lola_provider_replay_events" not in s
    assert ") ON CONFLICT DO NOTHING;" not in s


def test_immutable_recovery_ledger_is_private_and_no_browser_rpc_access():
    s=migration()
    assert "ALTER TABLE public.lola_provider_replay_recoveries ENABLE ROW LEVEL SECURITY;" in s
    assert "GRANT SELECT,INSERT ON public.lola_provider_replay_recoveries TO service_role;" in s
    assert "FROM PUBLIC,anon,authenticated,service_role;" in s
    assert "TO service_role;" in s
    assert "GRANT DELETE" not in s
    assert "GRANT UPDATE ON public.lola_provider_replay_recoveries" not in s
    assert "GRANT EXECUTE ON FUNCTION public.lola_recover_expired_gmail_replay(" in s


def test_post_reset_events_inherit_current_epoch_not_initial_epoch():
    s=migration()
    assert "CREATE TRIGGER lola_provider_replay_event_epoch_gate" in s
    assert "IF NEW.recovery_epoch=0 AND v_epoch>0 THEN" in s
    assert "NEW.recovery_epoch:=v_epoch;" in s
    assert "NEW.recovery_epoch IS DISTINCT FROM v_epoch" in s
    assert "FOR SHARE;" in s


def test_recovery_stays_metadata_only_no_external_send():
    s=migration().lower()
    for operation in ("sendmail", "send_approved_email", "http_post",
                      "quickbooks", "whatsapp_send", "drop table"):
        assert operation not in s
