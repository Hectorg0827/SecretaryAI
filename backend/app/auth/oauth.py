"""
QuickBooks Online and Google OAuth 2.0 flow handlers.
"""
import secrets
from urllib.parse import urlencode

import httpx

from app.config import get_settings

settings = get_settings()

QBO_AUTH_URL = "https://appcenter.intuit.com/connect/oauth2"
QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
QBO_SCOPES = "com.intuit.quickbooks.accounting"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
# Gmail modify + Sheets read/write; offline for refresh token
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/spreadsheets "
    "https://www.googleapis.com/auth/drive.file"
)


def build_authorization_url(state: str) -> str:
    """Build the URL to redirect the user to for QBO OAuth consent."""
    params = {
        "client_id": settings.intuit_client_id,
        "scope": QBO_SCOPES,
        "redirect_uri": settings.intuit_redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{QBO_AUTH_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(code: str, realm_id: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            QBO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.intuit_redirect_uri,
            },
            auth=(settings.intuit_client_id, settings.intuit_client_secret),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        tokens = response.json()
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens["refresh_token"],
            "realm_id": realm_id,
            "expires_in": tokens.get("expires_in", 3600),
        }


async def revoke_token(token: str) -> bool:
    """Revoke a QBO token (for disconnect flow)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
            data={"token": token},
            auth=(settings.intuit_client_id, settings.intuit_client_secret),
        )
        return response.status_code == 200


# ─── Google OAuth ──────────────────────────────────────────────────────────────

def build_google_authorization_url(state: str) -> str:
    """Build the URL to redirect the user to for Google OAuth consent."""
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        "access_type": "offline",   # get a refresh token
        "prompt": "consent",        # always show consent so we always get refresh_token
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code_for_tokens(code: str) -> dict:
    """Exchange Google authorization code for access + refresh tokens."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.google_redirect_uri,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        tokens = response.json()
        return {
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", ""),
            "expires_in": tokens.get("expires_in", 3600),
        }


async def revoke_google_token(token: str) -> bool:
    """Revoke a Google token (for disconnect flow)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            GOOGLE_REVOKE_URL,
            params={"token": token},
        )
        return response.status_code == 200


def refresh_google_token_if_needed(credentials_dict: dict) -> tuple[dict, bool]:
    """
    Synchronously refresh the Google access token if it is expired or close to expiry.
    Uses google-auth's built-in refresh mechanism.

    Returns:
        (updated_credentials_dict, was_refreshed)
        - updated_credentials_dict has the new "token" value if refreshed
        - was_refreshed is True if a refresh was performed

    This is synchronous because it is called from sync FastAPI dependencies and
    Celery tasks. The underlying HTTP call is quick (single POST to Google).
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_info(credentials_dict)

        # Skip if still valid
        if creds.valid:
            return credentials_dict, False

        # Can't refresh without a refresh token
        if not creds.refresh_token:
            return credentials_dict, False

        creds.refresh(Request())

        updated = dict(credentials_dict)
        updated["token"] = creds.token
        return updated, True

    except Exception:
        # If refresh fails, return original — caller handles the downstream error
        return credentials_dict, False
