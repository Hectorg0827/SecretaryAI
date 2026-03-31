"""
Request timeout middleware — enforces a hard wall-clock limit per request.

If an upstream handler (route + dependencies) takes longer than the configured
threshold the middleware returns 504 Gateway Timeout rather than letting the
connection hang indefinitely.

This is a safety net for runaway DB queries or slow external-API calls that
slip past the service-layer timeouts in individual handlers.

Default: 30 seconds.  Override with TIMEOUT_SECONDS env var or by passing
`timeout_seconds` to the constructor.
"""
import asyncio
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30  # seconds


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Returns 504 if the downstream handler exceeds the timeout."""

    def __init__(self, app, timeout_seconds: float = _DEFAULT_TIMEOUT):
        super().__init__(app)
        self._timeout = timeout_seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        try:
            return await asyncio.wait_for(call_next(request), timeout=self._timeout)
        except asyncio.TimeoutError:
            path = request.url.path
            log.warning(
                "Request timed out after %.1fs  path=%s  method=%s",
                self._timeout,
                path,
                request.method,
            )
            return JSONResponse(
                status_code=504,
                content={
                    "error": "gateway_timeout",
                    "message": f"Request exceeded {self._timeout:.0f}s timeout.",
                },
            )
