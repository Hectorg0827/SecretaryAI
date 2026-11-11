from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "SecretaryAI"
    debug: bool = False
    secret_key: str
    # Dedicated key for encrypting stored tokens (QuickBooks, Google, Microsoft,
    # 2FA secrets). Strongly recommended: without it SECRET_KEY does double duty
    # (signing logins AND encrypting tokens). Safe to add at any time — see
    # app/utils/encryption.py. To rotate, move the old value to
    # ENCRYPTION_KEY_PREVIOUS (comma-separated) and set a new ENCRYPTION_KEY.
    encryption_key: str = ""
    encryption_key_previous: str = ""

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    database_url: str

    # Anthropic / Claude
    anthropic_api_key: str
    # claude_model pins the Sonnet-tier version. Haiku tier is always pinned to
    # claude-haiku-4-5-20251001 via model_router.py and is not overridable here
    # (prevents accidental cost blowout from operator misconfiguration).
    claude_model: str = "claude-sonnet-4-6"

    # Conductor (QB Desktop)
    conductor_api_key: str = ""
    # Shared secret Conductor sends in X-Conductor-Secret on webhook calls.
    # Its own value — never the API key. Conductor webhooks are rejected until set.
    conductor_webhook_secret: str = ""

    # Intuit / QuickBooks Online
    intuit_client_id: str = ""
    intuit_client_secret: str = ""
    intuit_redirect_uri: str = ""
    intuit_environment: str = "production"  # or "sandbox"
    # Webhook verifier token from the Intuit developer portal; QBO webhooks are
    # rejected (fail-closed) until this is set.
    intuit_webhook_verifier_token: str = ""

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
    # If Redis (session revocation store) is unreachable, authenticated
    # requests are refused. Set true only if you accept that logged-out or
    # removed users may get back in during a Redis outage.
    auth_fail_open_on_store_outage: bool = False
    # When true, a new account cannot sign in until its email address has been
    # confirmed. Needs SENDGRID_API_KEY (the confirmation email must be
    # deliverable), which is checked at startup.
    require_email_verification: bool = False

    # Google APIs (Gmail + Sheets)
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""

    # Microsoft (Outlook / Microsoft 365 mail). Register an app in Azure
    # ("Accounts in any organizational directory and personal Microsoft
    # accounts"), then set all three. MS_TENANT stays "common" unless the app
    # is restricted to one organisation.
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_redirect_uri: str = ""
    ms_tenant: str = "common"

    # Computer Use — OFF unless an operator explicitly turns it on. The engine
    # drives the mouse/keyboard of the machine the backend runs on, so it must
    # never be on by default in a shared cloud deployment.
    computer_use_enabled: bool = False
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
    # The paywall switch. When true, the product pages need an active
    # subscription or a running trial (new companies get a 14-day trial).
    # Refused at startup unless Stripe is fully configured, so it can never
    # lock customers out with no way to pay.
    billing_enforced: bool = False

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

        if self.billing_enforced and not (self.stripe_secret_key and self.stripe_webhook_secret):
            errors.append(
                "BILLING_ENFORCED is on but STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET are not set — "
                "customers would be blocked with no way to pay"
            )

        if self.encryption_key and len(self.encryption_key) < 32:
            errors.append("ENCRYPTION_KEY must be at least 32 characters")
        if self.encryption_key and self.encryption_key == self.secret_key:
            errors.append("ENCRYPTION_KEY must be different from SECRET_KEY")

        if self.require_email_verification and not self.sendgrid_api_key:
            errors.append(
                "REQUIRE_EMAIL_VERIFICATION is on but SENDGRID_API_KEY is not set — "
                "nobody could receive the confirmation email, so nobody could sign in"
            )

        ms_fields = {
            "MS_CLIENT_ID": self.ms_client_id,
            "MS_CLIENT_SECRET": self.ms_client_secret,
            "MS_REDIRECT_URI": self.ms_redirect_uri,
        }
        ms_set = {k for k, v in ms_fields.items() if v}
        if ms_set and len(ms_set) != len(ms_fields):
            errors.append(
                f"Partial Microsoft config: {sorted(set(ms_fields) - ms_set)} must also be set when using Outlook"
            )

        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors))

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
