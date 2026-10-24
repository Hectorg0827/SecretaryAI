from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache
from urllib.parse import urlsplit
import re


class Settings(BaseSettings):
    # App
    app_name: str = "SecretaryAI"
    debug: bool = False
    secret_key: str

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    database_url: str

    # A separately deployed staging stack must name its Supabase project
    # explicitly. These values are not secrets; never reuse Lyo or production.
    secretaryai_deployment_env: str = ""
    secretaryai_expected_supabase_project_ref: str = ""
    secretaryai_email_delivery_mode: str = "disabled"
    secretaryai_live_email_delivery_approved: bool = False
    secretaryai_staging_email_pilot_enabled: bool = False

    # Anthropic / Claude
    anthropic_api_key: str
    # claude_model pins the Sonnet-tier version. Haiku tier is always pinned to
    # claude-haiku-4-5-20251001 via model_router.py and is not overridable here
    # (prevents accidental cost blowout from operator misconfiguration).
    claude_model: str = "claude-sonnet-4-6"

    # Conductor (QB Desktop)
    conductor_api_key: str = ""

    # Intuit / QuickBooks Online
    intuit_client_id: str = ""
    intuit_client_secret: str = ""
    intuit_redirect_uri: str = ""
    intuit_environment: str = "production"  # or "sandbox"
    # Webhook verifier token from the Intuit developer portal; QBO webhooks are
    # rejected (fail-closed) until this is set.
    intuit_webhook_verifier_token: str = ""

    # Optional local ClamAV daemon socket for private evidence scanning.
    # Blank means scanning fails closed; never use public TCP clamd.
    clamav_unix_socket: str = ""

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # SendGrid
    sendgrid_api_key: str = ""
    from_email: str = "secretary@secretaryai.com"
    # Optional comma-separated domain allow-list for auto-sent (NOTIFY) alert
    # emails. When set, alerts may only go to these domains. Empty = no restriction.
    alert_email_allowlist: str = ""

    # Security
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    api_key_rotation_hours: int = 24

    # Google APIs (Gmail + Sheets)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Computer Use safety
    computer_use_enabled: bool = True
    computer_use_max_steps: int = 50
    computer_use_screenshot_width: int = 1280
    computer_use_screenshot_height: int = 720

    # CORS — comma-separated list of allowed origins
    # Set to "*" to allow all (useful for mobile/dev). In production list explicit origins.
    cors_origins: str = "http://localhost:5173,https://app.secretaryai.com,http://localhost:8081,http://localhost:19006"

    # OpenAI (optional — used for Whisper speech-to-text transcription)
    openai_api_key: str = ""

    # Industry Module — controls which intelligence module is loaded
    default_industry_module: str = "wholesale_distribution"

    # Frontend base URL — used for OAuth redirect responses (QBO callback)
    frontend_url: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_publishable_key: str = ""

    # Billing master switch.
    #
    # When true (the default) the paid routers in app/main.py are gated behind
    # require_active_subscription, and a production start REFUSES unless Stripe
    # is configured — see the validator below. The two have to move together:
    # a paywalled deployment with no Stripe is a product nobody can use and
    # nobody can buy, because every route returns 402 and checkout hands back a
    # mock URL.
    #
    # Set it to false only for an internal or pilot deployment that is
    # deliberately free. The app then logs, loudly and on every start, that it
    # is serving the whole product for nothing.
    billing_enabled: bool = True

    # Sentry
    sentry_dsn: str = ""

    # API docs — set to a secret string to expose /docs in production behind X-Docs-Key header
    # Leave empty to disable /docs entirely in production (docs still available in debug mode)
    docs_api_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False
        # An operator-added key that this model doesn't know about (a typo, or a
        # var meant for another service) must not crash the API at startup.
        extra = "ignore"

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        errors: list[str] = []

        # Secret key must be strong enough to be used as an AES-256 key seed
        if len(self.secret_key) < 32:
            errors.append(
                f"SECRET_KEY must be at least 32 characters (got {len(self.secret_key)}). "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        # Staging is an isolated control plane: reject cross-project credentials
        # before any database connection, email call or worker startup. Supabase
        # direct URLs have the project ref in the hostname; pooler URLs encode
        # it in the username (postgres.<project-ref>).
        if self.secretaryai_deployment_env.strip().lower() == "staging":
            ref = self.secretaryai_expected_supabase_project_ref.strip()
            if not re.fullmatch(r"[a-z0-9]{20}", ref):
                errors.append(
                    "Staging requires SECRETARYAI_EXPECTED_SUPABASE_PROJECT_REF "
                    "set to the isolated Supabase project's 20-character ref."
                )
            else:
                try:
                    api = urlsplit(self.supabase_url)
                    if api.scheme != "https" or api.hostname != f"{ref}.supabase.co":
                        errors.append(
                            "SUPABASE_URL does not match the expected staging project."
                        )
                except ValueError:
                    errors.append("SUPABASE_URL is invalid for isolated staging.")

                try:
                    db = urlsplit(self.database_url)
                    db_host = db.hostname or ""
                    db_user = db.username or ""
                    matches_direct = db_host == f"db.{ref}.supabase.co"
                    matches_pooler = (
                        db_host.endswith(".pooler.supabase.com")
                        and db_user == f"postgres.{ref}"
                    )
                    if (db.scheme not in ("postgresql", "postgres")
                            or not (matches_direct or matches_pooler)):
                        errors.append(
                            "DATABASE_URL does not target the expected isolated "
                            "staging Supabase PostgreSQL project."
                        )
                except ValueError:
                    errors.append("DATABASE_URL is invalid for isolated staging.")

            if self.intuit_environment != "sandbox":
                errors.append("Staging requires INTUIT_ENVIRONMENT=sandbox.")
            if self.computer_use_enabled:
                errors.append("Staging requires COMPUTER_USE_ENABLED=false.")
            if (self.secretaryai_email_delivery_mode.strip().lower() != "disabled"
                or self.secretaryai_live_email_delivery_approved
                or self.secretaryai_staging_email_pilot_enabled):
                errors.append(
                    "Staging API requires email delivery and pilot workers disabled. "
                    "Pilot execution must use the separate controlled release procedure."
                )
            if self.billing_enabled and self.stripe_secret_key and not self.stripe_secret_key.startswith("sk_test_"):
                errors.append("Staging billing requires a Stripe test-mode secret key.")

        # If QBO integration is partially configured, require all three fields
        qbo_fields = {
            "INTUIT_CLIENT_ID": self.intuit_client_id,
            "INTUIT_CLIENT_SECRET": self.intuit_client_secret,
            "INTUIT_REDIRECT_URI": self.intuit_redirect_uri,
        }
        qbo_set = {k for k, v in qbo_fields.items() if v}
        if qbo_set and len(qbo_set) != len(qbo_fields):
            missing = sorted(set(qbo_fields) - qbo_set)
            errors.append(
                f"Partial QBO config: {missing} must also be set when using QuickBooks Online"
            )

        # Same for Google OAuth
        google_fields = {
            "GOOGLE_CLIENT_ID": self.google_client_id,
            "GOOGLE_CLIENT_SECRET": self.google_client_secret,
            "GOOGLE_REDIRECT_URI": self.google_redirect_uri,
        }
        google_set = {k for k, v in google_fields.items() if v}
        if google_set and len(google_set) != len(google_fields):
            missing = sorted(set(google_fields) - google_set)
            errors.append(
                f"Partial Google config: {missing} must also be set when using Gmail/Sheets"
            )

        # Billing must not be half-configured in production.
        #
        # Before this check, app/api/billing.py fell back to MOCK mode whenever
        # STRIPE_SECRET_KEY was absent: /billing/checkout returned
        # https://mock-stripe.example.com/... and webhooks were ignored. In
        # production that is indistinguishable from working software until
        # someone notices no money has arrived. With the paywall now live, it is
        # worse — every paid route answers 402 and there is no way to pay.
        #
        # So: fail at startup, where it is unmissable, rather than at the first
        # customer's checkout.
        if self.billing_enabled and not self.debug:
            if not self.stripe_secret_key:
                errors.append(
                    "BILLING_ENABLED is true and DEBUG is false, but "
                    "STRIPE_SECRET_KEY is not set. Billing would silently run in "
                    "mock mode: checkout would return a fake URL and no customer "
                    "could pay, while every paid route returns 402. Set "
                    "STRIPE_SECRET_KEY (Stripe Dashboard > Developers > API keys), "
                    "or set BILLING_ENABLED=false to run deliberately free."
                )
            if not self.stripe_webhook_secret:
                errors.append(
                    "BILLING_ENABLED is true and DEBUG is false, but "
                    "STRIPE_WEBHOOK_SECRET is not set. Stripe webhooks would be "
                    "rejected, so subscriptions would never activate, renew or "
                    "cancel in this database and paying customers would still be "
                    "refused. Set STRIPE_WEBHOOK_SECRET (Stripe Dashboard > "
                    "Developers > Webhooks > your endpoint > Signing secret), or "
                    "set BILLING_ENABLED=false to run deliberately free."
                )

        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors))

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
