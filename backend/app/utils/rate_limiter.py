"""
Redis-backed sliding window rate limiter.

Uses an atomic Redis sorted set (ZRANGEBYSCORE + ZADD + EXPIRE) to count
requests per key across all worker processes. Safe for multi-worker Uvicorn
and multi-node deployments.

Falls back to in-process counting if Redis is unreachable (best-effort;
limits won't be cross-worker in that degraded state).
"""
import time
import logging
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


class RedisRateLimiter:
    """
    Sliding window counter backed by Redis sorted sets.
    Key: arbitrary string (e.g. "user_id:ip")
    Window: rolling `window_seconds` seconds
    Limit: `max_calls` per window
    """

    def __init__(self, max_calls: int, window_seconds: int, prefix: str = "rl"):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self.prefix = prefix
        # In-process fallback (used when Redis is unreachable)
        self._fallback: dict[str, deque] = defaultdict(deque)

    def _redis(self):
        import redis as redis_lib
        return redis_lib.from_url(settings.redis_url, socket_connect_timeout=0.5)

    def is_allowed(self, key: str) -> bool:
        full_key = f"{self.prefix}:{key}"
        now = time.time()
        window_start = now - self.window_seconds

        try:
            r = self._redis()
            pipe = r.pipeline()
            # Remove timestamps outside the window
            pipe.zremrangebyscore(full_key, 0, window_start)
            # Count remaining
            pipe.zcard(full_key)
            # Add current timestamp (score = timestamp, member = timestamp:random)
            pipe.zadd(full_key, {f"{now}": now})
            # Set TTL so key auto-expires
            pipe.expire(full_key, self.window_seconds + 1)
            results = pipe.execute()
            count = results[1]  # zcard result (before adding current)
            return count < self.max_calls

        except Exception as exc:
            log.debug("Redis rate limiter unavailable, using fallback: %s", exc)
            # In-process fallback
            window = self._fallback[key]
            while window and now - window[0] > self.window_seconds:
                window.popleft()
            if len(window) >= self.max_calls:
                return False
            window.append(now)
            return True

    def remaining(self, key: str) -> int:
        full_key = f"{self.prefix}:{key}"
        now = time.time()
        window_start = now - self.window_seconds
        try:
            r = self._redis()
            r.zremrangebyscore(full_key, 0, window_start)
            count = r.zcard(full_key)
            return max(0, self.max_calls - count)
        except Exception:
            window = self._fallback.get(key, deque())
            return max(0, self.max_calls - len(window))


# Pre-configured limiters
chat_limiter         = RedisRateLimiter(max_calls=30,  window_seconds=60,  prefix="rl:chat")
computer_use_limiter = RedisRateLimiter(max_calls=5,   window_seconds=60,  prefix="rl:cu")
api_limiter          = RedisRateLimiter(max_calls=120, window_seconds=60,  prefix="rl:api")
login_limiter        = RedisRateLimiter(max_calls=5,   window_seconds=60,  prefix="rl:login")


def get_client_key(request: Request) -> str:
    """Build a rate limit key from user ID + IP."""
    user_id = getattr(request.state, "user_id", None) or ""
    ip = request.client.host if request.client else "unknown"
    return f"{user_id}:{ip}"


def require_rate_limit(limiter: RedisRateLimiter):
    """FastAPI dependency that enforces a rate limit."""
    def dependency(request: Request):
        key = get_client_key(request)
        if not limiter.is_allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Please slow down.",
                headers={"Retry-After": str(limiter.window_seconds)},
            )
    return dependency
