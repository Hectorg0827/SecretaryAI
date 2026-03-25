"""
Tenant middleware — extracts company_id from JWT and attaches it to request.state.

Every request that reaches a protected endpoint will have:
  - request.state.company_id  (str UUID)
  - request.state.user_id     (str UUID — the JWT "sub" claim)
  - request.state.role        (str — defaults to "viewer")

DB queries already filter by company_id via Depends(get_current_user); this
middleware makes the tenant context explicit, auditable, and available in logs
before any route handler runs.
"""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
import logging

log = logging.getLogger(__name__)

# Paths that don't require tenant context
PUBLIC_PATHS = {
    "/health",
    "/docs",
    "/openapi.json",
    "/auth/login",
    "/auth/register",
    "/auth/refresh",
    "/webhooks/stripe",
    "/api/billing/plans",
    "/api/billing/webhook",
}


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip public paths
        if any(path.startswith(p) for p in PUBLIC_PATHS):
            return await call_next(request)

        # Extract company_id from JWT — it's in the Authorization header.
        # The actual JWT decode happens in auth/rbac.py get_current_user.
        # Here we just ensure it's threaded through request.state.
        # (The full decode is done in each endpoint via Depends(get_current_user))
        # This middleware adds structured logging for tenant context.

        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        company_id = None

        if token:
            try:
                import jwt as pyjwt
                from app.config import get_settings
                settings = get_settings()
                payload = pyjwt.decode(token, settings.secret_key, algorithms=["HS256"])
                company_id = payload.get("company_id")
                request.state.company_id = company_id
                request.state.user_id = payload.get("sub")
                request.state.role = payload.get("role", "viewer")
                log.debug(
                    "Tenant context attached",
                    extra={
                        "company_id": company_id,
                        "user_id": request.state.user_id,
                        "role": request.state.role,
                        "path": path,
                        "method": request.method,
                    },
                )
            except Exception:
                # Let the auth system handle invalid tokens — don't 401 here
                # because the endpoint's Depends(get_current_user) will do it
                # with a proper error message.
                pass

        response = await call_next(request)

        # Add tenant context to response headers for debugging (truncated for security)
        if company_id:
            response.headers["X-Tenant-ID"] = str(company_id)[:8] + "..."

        return response
