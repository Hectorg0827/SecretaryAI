"""
Browser sessions in a protected cookie.

The web app used to keep its sign-in token in the browser's localStorage,
where any script running on the page could read it. A browser client can now
ask (header `X-Session-Mode: cookie`) for the token to be delivered in an
httpOnly cookie instead:

  sai_session  httpOnly, Secure, SameSite=Lax — the session token. Page
               scripts cannot read it; the browser sends it by itself.
  sai_csrf     a random value tied to the session, also returned in the JSON
               body. The web app echoes it in `X-CSRF-Token` on every request
               that changes something. Another website can make the browser
               send the cookie, but cannot read this value, so it cannot forge
               such a request.

In cookie mode the token is never placed in a response body.

Desktop and mobile apps keep using `Authorization: Bearer` (they store the
token in the operating system's secure storage) and are unaffected: a request
that carries a Bearer token is never subject to the CSRF check.
"""
from __future__ import annotations

import hmac
import secrets
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings

SESSION_COOKIE = "sai_session"
CSRF_COOKIE = "sai_csrf"
CSRF_HEADER = "x-csrf-token"
MODE_HEADER = "x-session-mode"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def wants_cookie(request: Optional[Request]) -> bool:
    return request is not None and request.headers.get(MODE_HEADER, "").strip().lower() == "cookie"


def _cookie_args() -> dict:
    s = get_settings()
    return {
        "secure": not s.debug,          # HTTPS only, except local development
        "samesite": "lax",
        "path": "/",
        "max_age": int(s.refresh_token_expire_days * 86400 + s.access_token_expire_minutes * 60),
    }


def session_response(token: str, body: dict, status_code: int = 200) -> JSONResponse:
    """A JSON response that carries the session in cookies, never in the body."""
    csrf = secrets.token_urlsafe(32)
    payload = {k: v for k, v in body.items() if k not in ("access_token", "token_type")}
    payload.update({"session": "cookie", "csrf_token": csrf})
    response = JSONResponse(payload, status_code=status_code)
    args = _cookie_args()
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **args)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=True, **args)
    response.headers["Cache-Control"] = "no-store"
    return response


def clear_session(response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


def cookie_token(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    return request.cookies.get(SESSION_COOKIE) or None


def csrf_value(request: Request) -> Optional[str]:
    return request.cookies.get(CSRF_COOKIE) or None


def require_csrf(request: Request) -> None:
    """
    For a cookie-authenticated request that changes something: the
    X-CSRF-Token header must equal the session's CSRF cookie.
    """
    if request.method.upper() in _SAFE_METHODS:
        return
    expected = csrf_value(request) or ""
    provided = request.headers.get(CSRF_HEADER, "")
    if not expected or not provided or not hmac.compare_digest(expected.encode(), provided.encode()):
        raise HTTPException(status_code=403, detail="Your session could not be verified. Please reload the page and try again.")
