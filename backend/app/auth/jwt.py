"""
JWT session token management with JTI-based revocation.

On logout, the token's JTI (unique ID) is written to Redis with a TTL equal
to the token's remaining lifetime. get_current_user checks the blacklist on
every request.
"""
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from jwt.exceptions import PyJWTError
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"

_REVOKE_PREFIX = "jwt_revoked:"
log = logging.getLogger(__name__)


class RevocationStoreUnavailable(RuntimeError):
    """A JWT cannot be trusted while the revocation backend is unavailable."""


def _revocation_alarm(operation: str, exc: Exception) -> None:
    # CRITICAL log goes to the production log stream; Sentry receives an event
    # when configured. Operations must page on this signal or /health failures.
    log.critical("SECURITY Redis revocation store unavailable during %s", operation, exc_info=True)
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        log.exception("Sentry unavailable while reporting revocation outage")



def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    to_encode.setdefault("jti", str(uuid.uuid4()))  # unique token ID for revocation
    # Token *scope* separates full user sessions ("access") from special-purpose
    # tokens minted with the same key (2FA pre-auth "2fa_pending", long-lived
    # "connector"). get_current_user only accepts "access"; without this, any
    # HS256 token signed with SECRET_KEY was a full session.
    to_encode.setdefault("scope", "access")
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


# Reject unusually large caller-controlled JWTs before any signature operation.
# Ordinary HS256 session tokens are far smaller than 8 KiB.
MAX_TOKEN_LENGTH = 8192


def _decode_signed_token(token: str, *, allow_expired: bool = False) -> Optional[dict]:
    """Single signed-token validator for user sessions, 2FA and refresh.

    HS256 is the only allowed algorithm. Both fresh and expired decoders require
    an expiry and validate nbf/iat; the caller still enforces scope, actor
    revocation and the refresh window. Never parse JWTs without verifying HMAC.
    """
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN_LENGTH:
        return None
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[ALGORITHM],
            options={"verify_exp": not allow_expired, "require": ["exp"]},
        )
        # PyJWT validates expiry but accepts some numeric-string exp values;
        # the application requires an actual JSON integer timestamp.
        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, int) or exp <= 0:
            return None
        return payload
    except (PyJWTError, TypeError, ValueError):
        return None


def decode_access_token(token: str) -> Optional[dict]:
    return _decode_signed_token(token)


def decode_refresh_candidate(token: str) -> Optional[dict]:
    """Verified-signature token, expiry unchecked ONLY for bounded rotation."""
    return _decode_signed_token(token, allow_expired=True)


def revoke_token(token: str) -> None:
    """
    Add the token's JTI to the Redis revocation blacklist.
    TTL is set to the token's remaining lifetime so the key auto-expires.
    """
    payload = decode_access_token(token)
    if not payload:
        return

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return

    ttl = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        r.setex(f"{_REVOKE_PREFIX}{jti}", ttl, "1")
    except Exception as exc:
        _revocation_alarm("revoke_token", exc)
        raise RevocationStoreUnavailable("Redis revocation store unavailable") from exc


def is_token_revoked(jti: str) -> bool:
    """Return True if the JTI is on the Redis blacklist."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        return r.exists(f"{_REVOKE_PREFIX}{jti}") == 1
    except Exception as exc:
        _revocation_alarm("is_token_revoked", exc)
        raise RevocationStoreUnavailable("Redis revocation store unavailable") from exc


def blacklist_jti(jti: str, ttl_seconds: int) -> None:
    """
    Directly blacklist a JTI for `ttl_seconds`.

    Unlike revoke_token(), this does not decode/verify a token, so it works for
    already-expired tokens — required for single-use refresh-token rotation
    (invalidating the presented token when a new one is issued).
    """
    if not jti or ttl_seconds <= 0:
        return
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        r.setex(f"{_REVOKE_PREFIX}{jti}", int(ttl_seconds), "1")
    except Exception as exc:
        _revocation_alarm("blacklist_jti", exc)
        raise RevocationStoreUnavailable("Redis revocation store unavailable") from exc


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
