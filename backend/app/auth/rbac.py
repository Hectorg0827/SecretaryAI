"""
Role-Based Access Control.
Roles: owner, manager, sales_rep, back_office, viewer
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.jwt import decode_access_token
from app.db import get_db

# auto_error=False: a browser session may arrive in a cookie instead of the
# Authorization header (see app/auth/session_cookie.py). A request with
# neither is still rejected with 401 in session_token() below.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def session_token(request: Request = None, token: Optional[str] = Depends(oauth2_scheme)) -> str:
    """
    The caller's session token: `Authorization: Bearer` (desktop, mobile, API)
    or, failing that, the browser's httpOnly session cookie. A cookie session
    must also pass the CSRF check on any request that changes something.
    """
    if token:
        return token
    from app.auth.session_cookie import cookie_token, require_csrf
    cookie = cookie_token(request)
    if cookie:
        require_csrf(request)
        return cookie
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )

# Every permission is listed explicitly per role. There is NO wildcard: a role
# passes a check only if the permission name is in its own set. ("view_all"
# used to act as a super-permission inside require_permission, which silently
# gave managers owner-only powers such as billing, user and integration
# management.)
_DATA_PERMISSIONS: set[str] = {
    "view_all", "view_dashboard", "view_financials", "view_inventory",
    "view_orders", "view_customers", "view_own_accounts", "run_reports",
    "manage_inventory", "approve_actions", "trigger_actions",
    "assign_accounts",   # decide which sales rep looks after which customer
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    # Owner: everything, including the account-level powers below.
    "owner": _DATA_PERMISSIONS | {
        "manage_users",         # invite / remove staff, company settings
        "manage_billing",       # subscription and payment
        "manage_integrations",  # connect / disconnect QuickBooks, Gmail, Outlook
    },
    # Manager: runs the business day to day, but cannot change who has access,
    # how the company pays, or which outside systems are connected.
    "manager": set(_DATA_PERMISSIONS),
    "sales_rep": {
        "view_own_accounts", "view_inventory", "view_dashboard",
    },
    "back_office": {
        "view_inventory", "view_orders", "run_reports", "view_customers",
        "view_dashboard",
    },
    "viewer": {
        "view_dashboard",
    },
}


def has_permission(role: str | None, permission: str) -> bool:
    """True only if `permission` is explicitly granted to `role`."""
    return permission in ROLE_PERMISSIONS.get(role or "viewer", set())


def get_current_user(token: str = Depends(session_token), db=Depends(get_db)) -> dict:
    from app.auth.jwt import (
        AuthStoreUnavailable, is_session_superseded, is_token_revoked, session_stamp_ok,
    )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Only full user-session tokens may act as a user. Special-purpose tokens
    # signed with the same secret (2FA pre-auth "2fa_pending", connector
    # "connector", invitations, password resets) must never resolve here.
    # Tokens issued before the scope claim existed carry no "scope" and are
    # treated as "access" so existing sessions keep working.
    if payload.get("scope", "access") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not valid for this endpoint",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not payload.get("sub") or not payload.get("company_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Deactivated / demoted / password-changed users lose their sessions
        # immediately, not when the token happens to expire.
        if is_session_superseded(payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session ended; please sign in again",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # The token must have been built from the user's CURRENT account state
        # (role, active, password) — see session stamps in app/auth/jwt.py.
        if not session_stamp_ok(payload, db=db):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session ended; please sign in again",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except AuthStoreUnavailable:
        # We cannot tell whether this session was logged out or removed, so
        # we do not let it in.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in service temporarily unavailable. Please try again shortly.",
        )
    return payload


def require_permission(permission: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role", "viewer")
        if not has_permission(role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' does not have permission: {permission}",
            )
        return user
    return checker
