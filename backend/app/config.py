from pydantic_settings import BaseSettings
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

    # Sentry
    sentry_dsn: str = ""

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
