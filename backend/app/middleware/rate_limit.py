"""
Per-tenant rate limiting middleware using Redis sorted sets (sliding-window algorithm).

Limits are keyed by (company_id, endpoint-group) so each tenant has independent
buckets.  If Redis is unavailable the middleware is transparent — requests pass
through unthrottled rather than causing a hard outage.

Rate limit table (requests per minute, burst allowance):
  /api/chat      → 20 rpm + 5  burst
  /api/agent     → 30 rpm + 10 burst
  /api/logistics → 60 rpm + 20 burst
  default        → 120 rpm + 30 burst
"""
import time
import redis as redis_lib
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from app.config import get_settings

RATE_LIMITS: dict[str, tuple[int, int]] = {
    # endpoint_prefix: (requests_per_minute, burst)
    "/api/chat":      (20, 5),
    "/api/agent":     (30, 10),
    "/api/logistics": (60, 20),
    "default":        (120, 30),
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, redis_url: str = "redis://localhost:6379/0"):
        super().__init__(app)
        self._redis: redis_lib.Redis | None = None
        self._redis_url = redis_url

    def _get_redis(self) -> redis_lib.Redis | None:
        if self._redis is None:
            try:
                self._redis = redis_lib.from_url(self._redis_url, decode_responses=True)
                self._redis.ping()
            except Exception:
                self._redis = None
        return self._redis

    async def dispatch(self, request: Request, call_next) -> Response:
        r = self._get_redis()
        if r is None:
            # Redis unavailable — fail open so the app stays reachable
            return await call_next(request)

        company_id = getattr(request.state, "company_id", None) or "anonymous"
        path = request.url.path

        # Find the most-specific matching limit
        limit_per_min, burst = RATE_LIMITS["default"]
        for prefix, (lpm, b) in RATE_LIMITS.items():
            if prefix != "default" and path.startswith(prefix):
                limit_per_min, burst = lpm, b
                break

        # Build Redis key using the top-level path segment as the bucket label
        # e.g. /api/chat/stream  →  bucket "chat"
        parts = path.split("/")
        bucket = parts[2] if len(parts) > 2 else "root"
        key = f"rl:{company_id}:{bucket}"
        window = 60  # 1-minute sliding window (seconds)

        try:
            now = int(time.time())
            pipe = r.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, now - window)
            pipe.zcard(key)
            pipe.expire(key, window * 2)
            results = pipe.execute()
            count = results[2]

            if count > limit_per_min + burst:
                return JSONResponse(
                    {"detail": "Rate limit exceeded. Please slow down."},
                    status_code=429,
                    headers={
                        "Retry-After": "60",
                        "X-RateLimit-Limit": str(limit_per_min),
                        "X-RateLimit-Remaining": "0",
                    },
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit_per_min)
            response.headers["X-RateLimit-Remaining"] = str(max(0, limit_per_min - count))
            return response
        except Exception:
            # Any Redis error — fail open
            return await call_next(request)
