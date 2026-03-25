from pydantic_settings import BaseSettings
from pydantic import model_validator
from functools import lru_cache


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

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"

    # SendGrid
    sendgrid_api_key: str = ""
    from_email: str = "secretary@secretaryai.com"

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

    # Sentry
    sentry_dsn: str = ""

    # API docs — set to a secret string to expose /docs in production behind X-Docs-Key header
    # Leave empty to disable /docs entirely in production (docs still available in debug mode)
    docs_api_key: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False

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

        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors))

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
