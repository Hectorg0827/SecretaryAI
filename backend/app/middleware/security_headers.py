"""
Security headers middleware — adds hardening headers to every response.

Headers applied:
  Strict-Transport-Security  — HSTS; browsers switch to HTTPS permanently
  X-Content-Type-Options     — disables MIME-sniffing (prevents XSS via content injection)
  X-Frame-Options            — blocks clickjacking via iframes
  Referrer-Policy            — limits URL leakage in Referer header
  Permissions-Policy         — disables dangerous browser features
  Content-Security-Policy    — controls what resources the page can load

CSP is set to a strict default-src baseline. Adjust the policy via
CONTENT_SECURITY_POLICY env var if the frontend needs additional origins.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


_DEFAULT_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "   # unsafe-inline needed for React dev; tighten in prod with nonces
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https://*.supabase.co wss://*.supabase.co https://api.anthropic.com; "
    "frame-ancestors 'none';"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects standard security headers on every HTTP response."""

    def __init__(self, app, csp: str = _DEFAULT_CSP):
        super().__init__(app)
        self._csp = csp

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Content-Security-Policy"] = self._csp
        return response
