"""
JWT session token management with JTI-based revocation.

On logout, the token's JTI (unique ID) is written to Redis with a TTL equal
to the token's remaining lifetime. get_current_user checks the blacklist on
every request.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"

_REVOKE_PREFIX = "jwt_revoked:"


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode["exp"] = expire
    to_encode.setdefault("jti", str(uuid.uuid4()))  # unique token ID for revocation
    return jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


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
    except Exception:
        pass  # If Redis is down, revocation is best-effort


def is_token_revoked(jti: str) -> bool:
    """Return True if the JTI is on the Redis blacklist."""
    try:
        import redis as redis_lib
        r = redis_lib.from_url(settings.redis_url, decode_responses=True)
        return r.exists(f"{_REVOKE_PREFIX}{jti}") == 1
    except Exception:
        return False  # Fail open — don't lock out users on Redis failure


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
