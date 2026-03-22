"""
Request ID middleware — injects a unique trace ID into every request/response.

A UUID is generated per request and stored in:
  - request.state.request_id  (available to route handlers and dependencies)
  - X-Request-ID response header (visible to clients and reverse proxies)

Use request.state.request_id in log calls for end-to-end traceability:
    log.info("...", extra={"request_id": request.state.request_id})
"""
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attaches a UUID trace ID to every request and echoes it in the response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour upstream trace ID (load balancer, Nginx) if provided
        request_id = request.headers.get(_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[_HEADER] = request_id
        return response
