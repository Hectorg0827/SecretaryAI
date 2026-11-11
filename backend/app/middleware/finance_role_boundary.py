"""Deny-by-default API boundary for narrowly scoped finance employee sessions.

Older routes may authenticate a user without checking a department permission.
Until every endpoint is permission-scoped, a finance-only identity must not be
able to reach those routes even with a perfectly valid session JWT.
"""

from fastapi.security.utils import get_authorization_scheme_param
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.jwt import decode_access_token


FINANCE_EMPLOYEE_ROLES = frozenset({
    "finance_controller", "ar_specialist", "ap_specialist", "pricing_billbacks",
})

# Method and path must match exactly. These three routes either validate the
# finance session against the current DB record (workspace), return the caller's
# own profile (me), or revoke the session (logout). The public login/refresh
# endpoints work without an Authorization header and remain available.
FINANCE_ALLOWED_ENDPOINTS = frozenset({
    ("GET", "/api/finance/workspace"),
    ("GET", "/auth/me"),
    ("POST", "/auth/logout"),
})


class FinanceRoleBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        scheme, bearer = get_authorization_scheme_param(
            request.headers.get("authorization", "")
        )
        if scheme.lower() == "bearer" and bearer:
            # Verify signature and expiry using the same decoder as RBAC. A
            # self-authored role claim must never activate this rule.
            payload = decode_access_token(bearer)
            if (payload and payload.get("scope", "access") == "access"
                    and payload.get("role") in FINANCE_EMPLOYEE_ROLES
                    and (request.method, request.url.path) not in FINANCE_ALLOWED_ENDPOINTS):
                return JSONResponse(
                    {"detail": "Finance employee role cannot access this endpoint"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)
