"""No staging API may boot against another project's Supabase/Postgres.

These test strings are synthetic; no service credentials are used or emitted.
"""
import pytest
from pydantic import ValidationError

from app.config import Settings

STAGING_REF = "xerktvdgjvjozvckjyfp"
OTHER_REF = "abcdefghijklmnopqrst"


def valid_staging(**overrides):
    values = {
        "secret_key": "a" * 64,
        "supabase_url": f"https://{STAGING_REF}.supabase.co",
        "supabase_anon_key": "synthetic-anon",
        "supabase_service_role_key": "synthetic-service-role",
        "database_url": (
            f"postgresql://postgres.{STAGING_REF}:synthetic@"
            "aws-0-us-east-1.pooler.supabase.com:6543/postgres"
        ),
        "anthropic_api_key": "synthetic-anthropic",
        "secretaryai_deployment_env": "staging",
        "secretaryai_expected_supabase_project_ref": STAGING_REF,
        "secretaryai_email_delivery_mode": "disabled",
        "secretaryai_live_email_delivery_approved": False,
        "secretaryai_staging_email_pilot_enabled": False,
        "computer_use_enabled": False,
        "intuit_environment": "sandbox",
        "billing_enabled": True,
        "stripe_secret_key": "sk_test_synthetic_not_valid",
        "stripe_webhook_secret": "whsec_synthetic_not_valid",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_staging_pooler_and_isolated_project_match():
    assert valid_staging().secretaryai_expected_supabase_project_ref == STAGING_REF


def test_staging_direct_postgres_connection_is_supported():
    settings = valid_staging(
        database_url=f"postgresql://postgres:synthetic@db.{STAGING_REF}.supabase.co:5432/postgres"
    )
    assert settings.secretaryai_deployment_env == "staging"


@pytest.mark.parametrize("change,expected", [
    ({"secretaryai_expected_supabase_project_ref": ""}, "EXPECTED_SUPABASE_PROJECT_REF"),
    ({"supabase_url": f"https://{OTHER_REF}.supabase.co"}, "SUPABASE_URL"),
    ({"supabase_url": "https://example.org"}, "SUPABASE_URL"),
    ({"database_url": f"postgresql://postgres:pw@db.{OTHER_REF}.supabase.co/postgres"}, "DATABASE_URL"),
    ({"database_url": f"postgresql://postgres.{OTHER_REF}:pw@aws-0-us-east-1.pooler.supabase.com/postgres"}, "DATABASE_URL"),
    ({"database_url": "postgresql://postgres:pw@localhost/postgres"}, "DATABASE_URL"),
    ({"intuit_environment": "production"}, "INTUIT_ENVIRONMENT"),
    ({"computer_use_enabled": True}, "COMPUTER_USE_ENABLED"),
    ({"secretaryai_email_delivery_mode": "live"}, "email delivery"),
    ({"secretaryai_live_email_delivery_approved": True}, "email delivery"),
    ({"secretaryai_staging_email_pilot_enabled": True}, "email delivery"),
    ({"stripe_secret_key": "sk_live_synthetic_not_valid"}, "test-mode"),
])
def test_staging_rejects_unsafe_cross_environment_configuration(change, expected):
    with pytest.raises(ValidationError) as err:
        valid_staging(**change)
    assert expected in str(err.value)


def test_no_staging_ref_requirement_for_production_config():
    # This test only verifies absence of the staging-specific hostname gate.
    # Existing billing / strength checks still apply.
    config = valid_staging(
        secretaryai_deployment_env="production",
        secretaryai_expected_supabase_project_ref="",
        supabase_url=f"https://{OTHER_REF}.supabase.co",
        database_url=f"postgresql://postgres:pw@db.{OTHER_REF}.supabase.co/postgres",
    )
    assert config.secretaryai_deployment_env == "production"
