"""
JWT session token management with server-side revocation.

Two kinds of revocation, both stored in Redis:

* per token  — on logout or refresh the token's JTI (unique ID) is blacklisted
  until the token could no longer be refreshed.
* per user   — when a user is deactivated, has their role changed, or changes
  their password, every token issued to them BEFORE that moment stops working
  (`sessions_valid_from`). This is what makes "remove this person" immediate
  instead of waiting for the token to expire.

Fail-closed: if Redis cannot be reached, the revocation state is unknown, so
authenticated requests are refused with 503 rather than letting a logged-out
or removed user back in. An operator can opt out with
AUTH_FAIL_OPEN_ON_STORE_OUTAGE=true (not recommended).
"""
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()
ALGORITHM = "HS256"

_REVOKE_PREFIX = "jwt_revoked:"
_USER_VALID_FROM_PREFIX = "sessions_valid_from:"

_redis_client = None


class AuthStoreUnavailable(RuntimeError):
    """Redis (the session-revocation store) could not be reached."""


def _redis():
    """Shared Redis client for auth state (one connection pool per process)."""
    global _redis_client
    if _redis_client is None:
        import redis as redis_lib
        _redis_client = redis_lib.from_url(
            settings.redis_url, decode_responses=True,
            socket_connect_timeout=1, socket_timeout=1,
        )
    return _redis_client


def _fail_open() -> bool:
    return bool(getattr(get_settings(), "auth_fail_open_on_store_outage", False))


def _session_horizon_seconds() -> int:
    """Longest time a token can still be used or refreshed after it was issued."""
    s = get_settings()
    return int(s.refresh_token_expire_days * 86400 + s.access_token_expire_minutes * 60)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    # Issued-at, with sub-second precision in `iat_ms`, so "revoke everything
    # issued before now" is exact.
    to_encode.setdefault("iat", int(now.timestamp()))
    to_encode.setdefault("iat_ms", int(now.timestamp() * 1000))
    to_encode.setdefault("jti", str(uuid.uuid4()))  # unique token ID for revocation
    # Token *scope* separates full user sessions ("access") from special-purpose
    # tokens minted with the same key (2FA pre-auth "2fa_pending", long-lived
    # "connector"). get_current_user only accepts "access"; without this, any
    # HS256 token signed with SECRET_KEY was a full session.
    to_encode.setdefault("scope", "access")
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


# ── Per-token revocation ──────────────────────────────────────────────────────

def revoke_token(token: str) -> None:
    """
    Blacklist the token's JTI (logout).

    The entry lives as long as the token could still be *refreshed*, not just
    until it expires — otherwise a logged-out token could be exchanged for a
    fresh one once the blacklist entry had lapsed.

    Raises AuthStoreUnavailable if the blacklist cannot be written.
    """
    payload = decode_access_token(token)
    if not payload:
        return

    jti = payload.get("jti")
    exp = payload.get("exp")
    if not jti or not exp:
        return

    remaining = max(1, int(exp - datetime.now(timezone.utc).timestamp()))
    blacklist_jti(jti, remaining + int(get_settings().refresh_token_expire_days * 86400))


def is_token_revoked(jti: str) -> bool:
    """True if the JTI is blacklisted. Raises AuthStoreUnavailable on outage."""
    try:
        return _redis().exists(f"{_REVOKE_PREFIX}{jti}") == 1
    except Exception as exc:
        if _fail_open():
            return False
        raise AuthStoreUnavailable("session store unavailable") from exc


def blacklist_jti(jti: str, ttl_seconds: int) -> None:
    """
    Directly blacklist a JTI for `ttl_seconds`.

    Unlike revoke_token(), this does not decode/verify a token, so it works for
    already-expired tokens — required for single-use refresh-token rotation.
    Raises AuthStoreUnavailable if the entry cannot be written.
    """
    if not jti or ttl_seconds <= 0:
        return
    try:
        _redis().setex(f"{_REVOKE_PREFIX}{jti}", int(ttl_seconds), "1")
    except Exception as exc:
        if _fail_open():
            return
        raise AuthStoreUnavailable("session store unavailable") from exc


def claim_jti_once(jti: str, ttl_seconds: int) -> bool:
    """
    Atomically mark a JTI as used. Returns True for the first caller only.

    Used for single-use tokens (refresh rotation, invitations, password
    resets): two requests presenting the same token cannot both succeed.
    """
    if not jti:
        return False
    try:
        return bool(_redis().set(f"{_REVOKE_PREFIX}{jti}", "1", nx=True, ex=max(1, int(ttl_seconds))))
    except Exception as exc:
        if _fail_open():
            return True
        raise AuthStoreUnavailable("session store unavailable") from exc


# ── Per-user revocation ───────────────────────────────────────────────────────

def revoke_user_sessions(user_id: str) -> None:
    """
    End every session of a user: tokens issued up to now stop working.
    Call when a user is deactivated, their role changes, or their password
    changes. Raises AuthStoreUnavailable if it cannot be recorded.
    """
    if not user_id:
        return
    try:
        _redis().setex(
            f"{_USER_VALID_FROM_PREFIX}{user_id}",
            _session_horizon_seconds(),
            str(int(time.time() * 1000)),
        )
    except Exception as exc:
        raise AuthStoreUnavailable("session store unavailable") from exc


def now_ms() -> int:
    return int(time.time() * 1000)


def sessions_ended_since(user_id: str, since_ms: int) -> bool:
    """
    True if this user's sessions were ended at or after `since_ms`.

    Sign-in and refresh read the user's row (role, active flag) and only then
    mint a token. If the account is changed in between, the token would carry
    the OLD role and be newer than the cut-off. Callers note the time before
    reading the row and call this just before returning a token: if a cut-off
    landed in that window, the token is not handed out. Together with the
    account-change code setting the cut-off both before and after its database
    update, no token built from pre-change data can survive the change.
    Raises AuthStoreUnavailable on outage (never fails open: a token is at stake).
    """
    if not user_id:
        return False
    try:
        raw = _redis().get(f"{_USER_VALID_FROM_PREFIX}{user_id}")
    except Exception as exc:
        raise AuthStoreUnavailable("session store unavailable") from exc
    if not raw:
        return False
    try:
        return int(raw) >= int(since_ms)
    except (TypeError, ValueError):
        return True


def is_session_superseded(payload: dict) -> bool:
    """
    True if this token was issued at or before its user's last "end all
    sessions" moment. Raises AuthStoreUnavailable on outage.
    """
    user_id = payload.get("sub")
    if not user_id:
        return False
    try:
        raw = _redis().get(f"{_USER_VALID_FROM_PREFIX}{user_id}")
    except Exception as exc:
        if _fail_open():
            return False
        raise AuthStoreUnavailable("session store unavailable") from exc
    if not raw:
        return False
    try:
        valid_from_ms = int(raw)
    except (TypeError, ValueError):
        return True
    issued_ms = payload.get("iat_ms")
    if issued_ms is None:
        iat = payload.get("iat")
        # Tokens from before iat existed cannot prove they are newer.
        if iat is None:
            return True
        issued_ms = int(iat) * 1000
    return int(issued_ms) <= valid_from_ms


# ── Passwords ────────────────────────────────────────────────────────────────
# bcrypt is used directly. (passlib 1.7.4 — unmaintained — breaks with
# bcrypt >= 4.1/5.0: every hash() raised, so nobody could register or log in
# on a fresh install where pip picked the current bcrypt.) Hashes are standard
# `$2b$` bcrypt, so passwords hashed by the old passlib setup still verify.

_BCRYPT_MAX_BYTES = 72  # bcrypt only ever looks at the first 72 bytes
BCRYPT_ROUNDS = 12      # work factor (tests lower this for speed)


def _password_bytes(password: str) -> bytes:
    return (password or "").encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed or not isinstance(hashed, str):
        return False
    try:
        return bcrypt.checkpw(_password_bytes(plain), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Not a bcrypt hash (corrupt or foreign value) — never a match.
        return False
