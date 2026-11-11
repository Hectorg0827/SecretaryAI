"""Gate 6C: reject stale user JWTs before legacy routes run (staging only).

Older handlers may check the JWT's signed role without looking up the current
employee record. Every staging Bearer *user* session is checked against the
active database row on every request; no cache may preserve removed privileges.
Connector tokens are separate machine credentials, permitted only on their
exact connector endpoints and validated there by get_connector_identity.
"""
from fastapi.security.utils import get_authorization_scheme_param
from hmac import compare_digest
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.api.deps import get_db
from app.auth.jwt import decode_access_token, is_token_revoked, password_session_version
from app.auth.rbac import ROLE_PERMISSIONS
from app.config import get_settings

# Login, refresh and password recovery authenticate their own credentials/body.
# They must remain reachable even if a prior session is stale or deactivated.
# CORS preflight is handled separately.
_SESSION_BOOTSTRAP = frozenset({
    ("POST", "/auth/login"),
    ("POST", "/auth/register"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/forgot-password"),
    ("POST", "/auth/reset-password"),
})
_CONNECTOR_MACHINE_ROUTES = frozenset({
    ("POST", "/api/connectors/heartbeat"),
    ("GET", "/api/connectors/tasks"),
    ("POST", "/api/connectors/task-result"),
    ("POST", "/api/connectors/sync-status"),
    ("POST", "/api/connectors/error"),
})


def _reject(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def _session_check(payload: dict) -> tuple[int, str] | None:
    """Require a non-revoked session whose tenant, role and active bit match DB."""
    user_id, company_id, role, jti = (
        payload.get("sub"), payload.get("company_id"),
        payload.get("role"), payload.get("jti"),
    )
    if (not all(isinstance(v, str) and v for v in (user_id, company_id, role, jti))
            or role not in ROLE_PERMISSIONS):
        return 401, "Invalid session"
    try:
        if is_token_revoked(jti):
            return 401, "Session revoked"
    except Exception:
        return 503, "Session verification unavailable"
    try:
        result = (
            get_db().table("users")
            .select("id,company_id,role,is_active,hashed_password")
            .eq("id", user_id).eq("company_id", company_id)
            .limit(2).execute()
        )
        rows = result.data
    except Exception:
        return 503, "Session verification unavailable"
    if (not isinstance(rows, list) or len(rows) != 1
            or not isinstance(rows[0], dict)
            or rows[0].get("id") != user_id
            or rows[0].get("company_id") != company_id
            or rows[0].get("role") != role
            or rows[0].get("is_active") is not True):
        return 403, "Session permissions changed; sign in again"
    # Legacy access JWTs without a credential fingerprint cannot be proven
    # safe following a password reset. Staging rollout requires a fresh login.
    fingerprint = payload.get("pwd")
    try:
        expected = password_session_version(user_id, rows[0].get("hashed_password"))
    except (TypeError, ValueError):
        return 503, "Session credential verification unavailable"
    if not isinstance(fingerprint, str) or not compare_digest(fingerprint, expected):
        return 401, "Session credentials changed; sign in again"
    return None


class ActiveSessionBoundaryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if get_settings().secretaryai_deployment_env.strip().lower() != "staging":
            return await call_next(request)
        method_path = (request.method, request.url.path)
        if request.method == "OPTIONS" or method_path in _SESSION_BOOTSTRAP:
            return await call_next(request)
        authorization = request.headers.get("authorization", "")
        scheme, token = get_authorization_scheme_param(authorization)
        # Basic credentials for docs, anonymous webhooks and unauthenticated
        # health checks keep their existing independent authentication rules.
        if scheme.lower() != "bearer":
            return await call_next(request)
        payload = decode_access_token(token)
        if not payload:
            return _reject(401, "Invalid or expired token")
        scope = payload.get("scope", "access")  # old user sessions lacked scope
        if scope == "connector" and method_path in _CONNECTOR_MACHINE_ROUTES:
            return await call_next(request)
        if scope != "access":
            return _reject(401, "Token is not valid for this endpoint")
        failure = await run_in_threadpool(_session_check, payload)
        if failure is not None:
            return _reject(*failure)
        return await call_next(request)
