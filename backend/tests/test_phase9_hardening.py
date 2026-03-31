"""
Phase 9 tests — Production hardening: middleware, error handlers, observability.

Covers:
  RequestIDMiddleware
    - Generates a UUID request-ID when none is provided
    - Honours an upstream X-Request-ID header
    - Echoes the ID in the response header

  SecurityHeadersMiddleware
    - Strict-Transport-Security present
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - Referrer-Policy present
    - Permissions-Policy present
    - Content-Security-Policy present

  RateLimitMiddleware
    - Passes through when Redis is unavailable (fail-open)
    - Returns 429 when the sliding-window count exceeds the limit
    - Rate-limit headers attached on non-throttled responses

  RedisRateLimiter (utils/rate_limiter.py)
    - is_allowed → True for first N calls within window
    - is_allowed → False after max_calls exceeded
    - Falls back to in-process counter when Redis raises

  register_error_handlers
    - RequestValidationError → 422
    - ValueError → 400
    - PermissionError → 403
    - Generic Exception → 500 with sanitized message (no traceback leaked)

  TimeoutMiddleware
    - Fast handler completes normally
    - Slow handler exceeds threshold → 504

  Observability helpers (utils/observability.py)
    - bind_request_context attaches request_id and company_id
    - task_logger attaches task_name and company_id
    - log_duration emits a timing record with duration_ms
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_request(path: str = "/", headers: dict | None = None) -> Request:
    """Build a minimal Starlette Request for unit tests."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


def _simple_app() -> FastAPI:
    """Minimal FastAPI app for middleware integration tests."""
    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"status": "ok"}

    return app


# ═══════════════════════════════════════════════════════════════════════════════
#  RequestIDMiddleware
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestIDMiddleware:
    def _app_with_middleware(self):
        from app.middleware.request_id import RequestIDMiddleware
        app = _simple_app()
        app.add_middleware(RequestIDMiddleware)
        return TestClient(app, raise_server_exceptions=False)

    def test_generates_uuid_when_no_header(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        rid = resp.headers.get("X-Request-ID")
        assert rid is not None
        # Must be a valid UUID
        uuid.UUID(rid)

    def test_honours_upstream_request_id(self):
        client = self._app_with_middleware()
        upstream = "my-trace-abc-123"
        resp = client.get("/ok", headers={"X-Request-ID": upstream})
        assert resp.headers["X-Request-ID"] == upstream

    def test_echoes_id_in_response(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert "X-Request-ID" in resp.headers

    def test_different_requests_get_different_ids(self):
        client = self._app_with_middleware()
        ids = {client.get("/ok").headers["X-Request-ID"] for _ in range(5)}
        assert len(ids) == 5


# ═══════════════════════════════════════════════════════════════════════════════
#  SecurityHeadersMiddleware
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityHeadersMiddleware:
    def _app_with_middleware(self):
        from app.middleware.security_headers import SecurityHeadersMiddleware
        app = _simple_app()
        app.add_middleware(SecurityHeadersMiddleware)
        return TestClient(app, raise_server_exceptions=False)

    def test_hsts_header_present(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert "Strict-Transport-Security" in resp.headers
        assert "max-age=" in resp.headers["Strict-Transport-Security"]

    def test_x_content_type_options_nosniff(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_deny(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_referrer_policy_present(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert "Referrer-Policy" in resp.headers

    def test_permissions_policy_present(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert "Permissions-Policy" in resp.headers

    def test_csp_present(self):
        client = self._app_with_middleware()
        resp = client.get("/ok")
        assert "Content-Security-Policy" in resp.headers

    def test_custom_csp_applied(self):
        from app.middleware.security_headers import SecurityHeadersMiddleware
        custom = "default-src 'none'"
        app = _simple_app()
        app.add_middleware(SecurityHeadersMiddleware, csp=custom)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/ok")
        assert resp.headers["Content-Security-Policy"] == custom


# ═══════════════════════════════════════════════════════════════════════════════
#  RateLimitMiddleware
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitMiddleware:
    def _app(self, redis_url: str = "redis://localhost:6379/0"):
        from app.middleware.rate_limit import RateLimitMiddleware
        app = _simple_app()
        app.add_middleware(RateLimitMiddleware, redis_url=redis_url)
        return app

    def test_fail_open_when_redis_unavailable(self):
        """Middleware must pass through when Redis is unreachable."""
        app = self._app(redis_url="redis://localhost:19999/0")  # nothing listening
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/ok")
        assert resp.status_code == 200

    def test_passes_through_under_limit(self):
        """When Redis returns count ≤ limit, response is 200 with rate-limit headers."""
        from app.middleware.rate_limit import RateLimitMiddleware

        mock_pipe = MagicMock()
        # pipeline.execute() returns [zadd_result, zremrange_result, count, expire_result]
        # count = 1 (well below default 120 + 30 burst)
        mock_pipe.execute.return_value = [1, 0, 1, True]

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        app = _simple_app()
        app.add_middleware(RateLimitMiddleware)

        with patch.object(RateLimitMiddleware, "_get_redis", return_value=mock_redis):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/ok")

        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers

    def test_429_when_limit_exceeded(self):
        """When Redis count > limit + burst, middleware returns 429."""
        from app.middleware.rate_limit import RateLimitMiddleware

        mock_pipe = MagicMock()
        # count = 999 — way above any configured limit
        mock_pipe.execute.return_value = [1, 0, 999, True]

        mock_redis = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe

        app = _simple_app()
        app.add_middleware(RateLimitMiddleware)

        with patch.object(RateLimitMiddleware, "_get_redis", return_value=mock_redis):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/ok")

        assert resp.status_code == 429
        assert resp.headers.get("Retry-After") == "60"


# ═══════════════════════════════════════════════════════════════════════════════
#  RedisRateLimiter (utility class)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRedisRateLimiter:
    def _limiter(self, max_calls: int = 3, window_seconds: int = 60):
        from app.utils.rate_limiter import RedisRateLimiter
        return RedisRateLimiter(max_calls=max_calls, window_seconds=window_seconds, prefix="test")

    def _mock_redis(self, existing_count: int):
        """Return a mock Redis whose pipeline returns `existing_count` for zcard."""
        pipe = MagicMock()
        # results: [zremrange, zcard, zadd, expire]
        pipe.execute.return_value = [0, existing_count, 1, True]
        r = MagicMock()
        r.pipeline.return_value = pipe
        return r

    def test_allowed_when_count_below_max(self):
        limiter = self._limiter(max_calls=5)
        r = self._mock_redis(existing_count=2)
        with patch.object(limiter, "_redis", return_value=r):
            assert limiter.is_allowed("user-1") is True

    def test_blocked_when_count_at_max(self):
        limiter = self._limiter(max_calls=3)
        r = self._mock_redis(existing_count=3)
        with patch.object(limiter, "_redis", return_value=r):
            assert limiter.is_allowed("user-1") is False

    def test_falls_back_to_in_process_on_redis_error(self):
        limiter = self._limiter(max_calls=2)

        def _raise():
            raise ConnectionError("Redis down")

        with patch.object(limiter, "_redis", side_effect=_raise):
            # First two calls should be allowed (in-process bucket)
            assert limiter.is_allowed("u-fallback") is True
            assert limiter.is_allowed("u-fallback") is True
            # Third call exceeds in-process limit
            assert limiter.is_allowed("u-fallback") is False

    def test_remaining_returns_non_negative(self):
        limiter = self._limiter(max_calls=10)
        r = MagicMock()
        r.zremrangebyscore.return_value = None
        r.zcard.return_value = 8
        with patch.object(limiter, "_redis", return_value=r):
            assert limiter.remaining("user-x") == 2

    def test_remaining_zero_when_exceeded(self):
        limiter = self._limiter(max_calls=5)
        r = MagicMock()
        r.zremrangebyscore.return_value = None
        r.zcard.return_value = 20
        with patch.object(limiter, "_redis", return_value=r):
            assert limiter.remaining("user-x") == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  register_error_handlers
# ═══════════════════════════════════════════════════════════════════════════════

class TestErrorHandlers:
    def _app_with_handlers(self):
        from fastapi import FastAPI
        from pydantic import BaseModel
        from app.utils.error_handler import register_error_handlers

        app = FastAPI()
        register_error_handlers(app)

        @app.get("/value-error")
        async def _val():
            raise ValueError("bad input here")

        @app.get("/permission-error")
        async def _perm():
            raise PermissionError("access denied")

        @app.get("/generic-error")
        async def _generic():
            raise RuntimeError("secret internal detail")

        @app.post("/validate")
        async def _validate(body: dict):
            return body

        return TestClient(app, raise_server_exceptions=False)

    def test_value_error_returns_400(self):
        client = self._app_with_handlers()
        resp = client.get("/value-error")
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    def test_permission_error_returns_403(self):
        client = self._app_with_handlers()
        resp = client.get("/permission-error")
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_generic_exception_returns_500(self):
        client = self._app_with_handlers()
        resp = client.get("/generic-error")
        assert resp.status_code == 500
        assert resp.json()["error"] == "internal_error"
        # Must NOT expose implementation details
        assert "secret internal detail" not in resp.text
        assert "RuntimeError" not in resp.text

    def test_generic_500_has_safe_message(self):
        client = self._app_with_handlers()
        resp = client.get("/generic-error")
        body = resp.json()
        assert "message" in body
        assert len(body["message"]) > 0


# ═══════════════════════════════════════════════════════════════════════════════
#  TimeoutMiddleware
# ═══════════════════════════════════════════════════════════════════════════════

class TestTimeoutMiddleware:
    def _app(self, timeout: float, handler_delay: float = 0.0):
        from app.middleware.timeout import TimeoutMiddleware
        app = FastAPI()
        app.add_middleware(TimeoutMiddleware, timeout_seconds=timeout)

        @app.get("/fast")
        async def fast():
            return {"ok": True}

        @app.get("/slow")
        async def slow():
            await asyncio.sleep(handler_delay)
            return {"ok": True}

        return TestClient(app, raise_server_exceptions=False)

    def test_fast_handler_succeeds(self):
        client = self._app(timeout=5.0, handler_delay=0.0)
        resp = client.get("/fast")
        assert resp.status_code == 200

    def test_slow_handler_returns_504(self):
        # Use a very short timeout; handler takes longer
        client = self._app(timeout=0.05, handler_delay=1.0)
        resp = client.get("/slow")
        assert resp.status_code == 504
        assert resp.json()["error"] == "gateway_timeout"

    def test_504_body_contains_timeout_value(self):
        client = self._app(timeout=0.05, handler_delay=1.0)
        resp = client.get("/slow")
        assert "0s" in resp.json()["message"] or "timeout" in resp.json()["message"].lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Observability helpers
# ═══════════════════════════════════════════════════════════════════════════════

class TestObservability:
    def _make_request_with_state(self, request_id="rid-1", company_id="co-1"):
        req = _make_request()
        req.state.request_id = request_id
        req.state.company_id  = company_id
        return req

    def test_bind_request_context_returns_logger_adapter(self):
        from app.utils.observability import bind_request_context
        req = self._make_request_with_state()
        log = bind_request_context(req)
        assert isinstance(log, logging.LoggerAdapter)

    def test_bind_request_context_attaches_request_id(self):
        from app.utils.observability import bind_request_context
        req = self._make_request_with_state(request_id="trace-xyz")
        log = bind_request_context(req)
        assert log.extra["request_id"] == "trace-xyz"

    def test_bind_request_context_attaches_company_id(self):
        from app.utils.observability import bind_request_context
        req = self._make_request_with_state(company_id="co-42")
        log = bind_request_context(req)
        assert log.extra["company_id"] == "co-42"

    def test_bind_request_context_defaults_when_state_missing(self):
        from app.utils.observability import bind_request_context
        req = _make_request()  # no state set
        log = bind_request_context(req)
        # Falls back to "-" not None/error
        assert log.extra["request_id"] == "-"
        assert log.extra["company_id"] == "-"

    def test_task_logger_attaches_task_name(self):
        from app.utils.observability import task_logger
        log = task_logger("my_task", company_id="co-5")
        assert log.extra["task_name"] == "my_task"
        assert log.extra["company_id"] == "co-5"

    def test_task_logger_defaults_company_id(self):
        from app.utils.observability import task_logger
        log = task_logger("my_task")
        assert log.extra["company_id"] == "-"

    def test_log_duration_emits_timing_record(self):
        from app.utils.observability import log_duration
        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        base = logging.getLogger("test.duration")
        base.addHandler(_Handler())
        base.setLevel(logging.DEBUG)

        with log_duration(base, "my_operation"):
            pass  # instant

        assert len(records) == 1
        assert records[0].getMessage() == "timing"
        assert hasattr(records[0], "duration_ms")
        assert records[0].duration_ms >= 0

    def test_log_duration_measures_time(self):
        import time
        from app.utils.observability import log_duration

        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        base = logging.getLogger("test.duration2")
        base.addHandler(_Handler())
        base.setLevel(logging.DEBUG)

        with log_duration(base, "slow_op"):
            time.sleep(0.05)

        assert records[0].duration_ms >= 45  # at least ~50ms measured

    def test_log_duration_label_attached(self):
        from app.utils.observability import log_duration

        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        base = logging.getLogger("test.duration3")
        base.addHandler(_Handler())
        base.setLevel(logging.DEBUG)

        with log_duration(base, "db_query"):
            pass

        assert records[0].label == "db_query"

    def test_log_duration_extra_fields_merged(self):
        from app.utils.observability import log_duration

        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        base = logging.getLogger("test.duration4")
        base.addHandler(_Handler())
        base.setLevel(logging.DEBUG)

        with log_duration(base, "fetch", extra={"table": "accounts"}):
            pass

        assert records[0].table == "accounts"

    def test_bound_logger_merges_extra_on_log_call(self):
        from app.utils.observability import bind_request_context

        records: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(record)

        base = logging.getLogger("test.bound")
        base.addHandler(_Handler())
        base.setLevel(logging.DEBUG)

        req = self._make_request_with_state(request_id="r-99", company_id="co-99")
        log = bind_request_context(req, logger_name="test.bound")
        log.info("hello", extra={"row_count": 7})

        assert len(records) == 1
        rec = records[0]
        assert rec.request_id == "r-99"
        assert rec.company_id  == "co-99"
        assert rec.row_count   == 7
