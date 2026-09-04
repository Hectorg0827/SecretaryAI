"""
Role-Based Access Control.
Roles: owner, manager, sales_rep, back_office, viewer
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.auth.jwt import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {
        "view_all", "manage_users", "approve_actions", "manage_billing",
        "view_financials", "trigger_actions", "run_reports",
    },
    "manager": {
        "view_all", "approve_actions", "trigger_actions", "view_financials",
        "run_reports",
    },
    "sales_rep": {
        "view_own_accounts", "view_inventory",
    },
    "back_office": {
        "view_inventory", "view_orders", "run_reports", "view_customers",
    },
    "viewer": {
        "view_dashboard",
    },
}


def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    from app.auth.jwt import is_token_revoked
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Only full user-session tokens may act as a user. Special-purpose tokens
    # signed with the same secret (2FA pre-auth "2fa_pending", connector
    # "connector") must never resolve here — previously a pre-auth token could
    # call /auth/2fa/setup + /verify and overwrite the victim's TOTP secret.
    # Tokens issued before the scope claim existed carry no "scope" and are
    # treated as "access" so existing sessions keep working.
    if payload.get("scope", "access") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not valid for this endpoint",
            headers={"WWW-Authenticate": "Bearer"},
        )
    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload


def require_permission(permission: str):
    def checker(user: dict = Depends(get_current_user)) -> dict:
        role = user.get("role", "viewer")
        allowed = ROLE_PERMISSIONS.get(role, set())
        # "view_all" is a super-permission — owner/manager can access everything
        if permission not in allowed and "view_all" not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{role}' does not have permission: {permission}",
            )
        return user
    return checker
