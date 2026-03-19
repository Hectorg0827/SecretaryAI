"""
API rate limiter using a simple in-memory sliding window.
Protects the chat endpoint and Computer Use endpoints from abuse.
"""
import time
from collections import defaultdict, deque
from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    """
    Token-bucket / sliding window rate limiter.
    Thread-safe for single-process deployments (Uvicorn with 1 worker).
    For multi-worker deployments, back this with Redis.
    """

    def __init__(self, max_calls: int, window_seconds: int):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._windows: dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str) -> bool:
        """Return True if the request is within the rate limit."""
        now = time.monotonic()
        window = self._windows[key]

        # Remove expired timestamps
        while window and now - window[0] > self.window_seconds:
            window.popleft()

        if len(window) >= self.max_calls:
            return False

        window.append(now)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        window = self._windows[key]
        while window and now - window[0] > self.window_seconds:
            window.popleft()
        return max(0, self.max_calls - len(window))


# Pre-configured limiters
chat_limiter = SlidingWindowRateLimiter(max_calls=30, window_seconds=60)       # 30 msgs/min
computer_use_limiter = SlidingWindowRateLimiter(max_calls=5, window_seconds=60) # 5 CU tasks/min
api_limiter = SlidingWindowRateLimiter(max_calls=120, window_seconds=60)        # 120 req/min


def get_client_key(request: Request) -> str:
    """Build a rate limit key from user ID + IP."""
    user_id = getattr(request.state, "user_id", None) or ""
    ip = request.client.host if request.client else "unknown"
    return f"{user_id}:{ip}"


def require_rate_limit(limiter: SlidingWindowRateLimiter):
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
