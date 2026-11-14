"""Deny-by-default API boundary for narrowly scoped finance employee sessions.

Older routes may authenticate a user without checking a department permission.
Until every endpoint is permission-scoped, a finance-only identity must not be
able to reach those routes even with a perfectly valid session JWT.
"""

from fastapi.security.utils import get_authorization_scheme_param
from uuid import UUID
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.jwt import decode_access_token


FINANCE_EMPLOYEE_ROLES = frozenset({
    "finance_controller", "ar_specialist", "ap_specialist", "pricing_billbacks",
})

# Method and path must match exactly. Finance users can read their scoped
# workspace and profile, revoke their session, and change their own password.
# Public login and refresh work without an Authorization header.
FINANCE_ALLOWED_ENDPOINTS = frozenset({
    ("GET", "/api/finance/workspace"),
    ("GET", "/auth/me"),
    ("POST", "/auth/logout"),
    ("POST", "/auth/change-password"),
})

# Dedicated pricing staff may read or preview only these analysis-only routes.
# Never broaden to a wildcard for Insights, Logistics, Purchasing or QuickBooks.
# The destination pricing handlers independently revalidate the current DB
# role, tenant and active bit; ActiveSessionBoundary checks all signed sessions.
PRICING_BILLBACKS_ENDPOINTS = frozenset({
    ("GET", "/api/insights/price-book"),
    ("GET", "/api/insights/pricing-suggestions"),
    ("POST", "/api/insights/pricing-suggestions/preview"),
    ("POST", "/api/insights/pricing-suggestions/supplier-increase"),
    ("POST", "/api/insights/price-waterfall/preview"),
    ("POST", "/api/insights/shipment-pricing/preview"),
})
ASSESSMENT_READ_PREFIX = "/api/insights/price-book/sources/assessments/"


def _pricing_billbacks_route(method: str, path: str) -> bool:
    """Allow the above exact routes and one canonical UUID assessment GET."""
    if (method, path) in PRICING_BILLBACKS_ENDPOINTS:
        return True
    if method != "GET" or not path.startswith(ASSESSMENT_READ_PREFIX):
        return False
    raw_id = path[len(ASSESSMENT_READ_PREFIX):]
    try:
        canonical_id = str(UUID(raw_id))
    except (ValueError, AttributeError):
        return False
    return raw_id.lower() == canonical_id


class FinanceRoleBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        scheme, bearer = get_authorization_scheme_param(
            request.headers.get("authorization", "")
        )
        if scheme.lower() == "bearer" and bearer:
            # Verify signature and expiry using the same decoder as RBAC. A
            # self-authored role claim must never activate this rule.
            payload = decode_access_token(bearer)
            method_path = (request.method, request.url.path)
            is_pricing_staff = payload and payload.get("role") == "pricing_billbacks"
            is_allowed = (method_path in FINANCE_ALLOWED_ENDPOINTS
                          or (is_pricing_staff and _pricing_billbacks_route(*method_path)))
            if (payload and payload.get("scope", "access") == "access"
                    and payload.get("role") in FINANCE_EMPLOYEE_ROLES
                    and not is_allowed):
                return JSONResponse(
                    {"detail": "Finance employee role cannot access this endpoint"},
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)
