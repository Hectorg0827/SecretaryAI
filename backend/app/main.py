import logging
import logging.config

import redis as redis_lib
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import get_settings
from app.api import chat, dashboard, accounts, inventory, settings as settings_router, webhooks, actions, auth, agent, notifications, inbox, workflows, feed, setup, computer_use as computer_use_router, billing
from app.api import docs as docs_router
from app.utils.error_handler import register_error_handlers
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.request_id import RequestIDMiddleware
from app.middleware.tenant import TenantMiddleware
from app.middleware.rate_limit import RateLimitMiddleware

settings = get_settings()

# ── Structured JSON logging ────────────────────────────────────────────────────
_LOG_FORMAT = "json" if not settings.debug else "text"

if _LOG_FORMAT == "json":
    try:
        from pythonjsonlogger import jsonlogger

        class _RequestContextFilter(logging.Filter):
            """Injects request_id into every log record if available."""
            def filter(self, record):
                if not hasattr(record, "request_id"):
                    record.request_id = "-"
                return True

        _handler = logging.StreamHandler()
        _handler.setFormatter(
            jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )
        _handler.addFilter(_RequestContextFilter())
        logging.root.setLevel(logging.INFO)
        logging.root.handlers = [_handler]
    except ImportError:
        logging.basicConfig(level=logging.INFO)
else:
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")

log = logging.getLogger(__name__)

# ── Sentry ─────────────────────────────────────────────────────────────────────
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.2,
        environment="development" if settings.debug else "production",
        send_default_pii=False,  # never attach cookies, headers, or user IP by default
        before_send=lambda event, hint: _scrub_sentry_event(event),
    )


# Substrings that mark a value as sensitive (case-insensitive) — matched against
# local-variable names and request header/cookie keys.
_SENSITIVE_KEY_MARKERS = (
    "password", "passwd", "secret", "token", "authorization", "auth", "cookie",
    "session", "api_key", "apikey", "key", "credential", "card", "cvv", "ssn",
    "email", "prompt", "screenshot", "otp", "2fa", "totp",
)


def _is_sensitive_key(name: str) -> bool:
    n = str(name).lower()
    return any(marker in n for marker in _SENSITIVE_KEY_MARKERS)


def _redact_mapping(mapping: dict) -> None:
    if not isinstance(mapping, dict):
        return
    for k in list(mapping.keys()):
        if _is_sensitive_key(k):
            mapping[k] = "[redacted]"


def _scrub_sentry_event(event: dict) -> dict:
    """
    Strip sensitive data before sending to Sentry: local vars in every stack
    frame, request headers, cookies, and request body. Fail-closed — if the
    structure is unexpected we still drop the request body.
    """
    try:
        # Scrub local variables in ALL frames of ALL exception values.
        for value in event.get("exception", {}).get("values", []):
            for frame in value.get("stacktrace", {}).get("frames", []):
                _redact_mapping(frame.get("vars", {}))

        # Scrub the request context: headers, cookies, and body.
        request = event.get("request")
        if isinstance(request, dict):
            _redact_mapping(request.get("headers", {}))
            _redact_mapping(request.get("cookies", {}))
            # Never ship request bodies (may contain email/accounting/PII/prompts).
            request.pop("data", None)

        # Never ship user PII beyond an opaque id.
        user = event.get("user")
        if isinstance(user, dict):
            for k in ("email", "ip_address", "username"):
                user.pop(k, None)
    except Exception:
        # If scrubbing itself fails, drop the potentially-sensitive request block.
        if isinstance(event, dict):
            event.pop("request", None)
    return event


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("SecretaryAI API starting up")
    yield
    log.info("SecretaryAI API shut down")


# ── Single source of truth for the release version ─────────────────────────────
# Imported from the package so every artifact/response reports one value.
from app import __version__ as APP_VERSION

# ── Docs & OpenAPI: closed by default in production ────────────────────────────
# In production the raw schema (/openapi.json) and the Swagger UI (/docs) are
# BOTH off unless a valid X-Docs-Key is presented — the schema is not public.
_EXPOSE_OPENAPI = settings.debug

app = FastAPI(
    title="SecretaryAI API",
    version=APP_VERSION,
    description="AI operations manager for importers and distributors",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    # Disable the default public /openapi.json in production; a key-gated route
    # is registered below instead.
    openapi_url="/openapi.json" if _EXPOSE_OPENAPI else None,
)

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse


def _docs_key_ok(request: Request) -> bool:
    expected = getattr(settings, "docs_api_key", "") or ""
    provided = request.headers.get("x-docs-key", "") or request.query_params.get("docs_key", "")
    return bool(expected) and provided == expected


if not _EXPOSE_OPENAPI:
    @app.get("/openapi.json", include_in_schema=False)
    async def guarded_openapi(request: Request):
        """Production OpenAPI schema — only for callers with a valid X-Docs-Key."""
        if not _docs_key_ok(request):
            return JSONResponse({"detail": "Not authorized."}, status_code=401)
        return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
async def custom_docs(request: Request):
    """API docs — accessible with X-Docs-Key header (value from DOCS_API_KEY env var)."""
    if not _docs_key_ok(request):
        return JSONResponse({"detail": "Not authorized. Include X-Docs-Key header."}, status_code=401)
    # Pass the key through so the browser can fetch the guarded schema.
    schema_url = "/openapi.json?docs_key=" + (getattr(settings, "docs_api_key", "") or "")
    return get_swagger_ui_html(openapi_url=schema_url, title="SecretaryAI API")


# ── Middleware (order matters — outermost first) ───────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(RateLimitMiddleware, redis_url=settings.redis_url)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_allow_all = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _cors_origins,
    allow_credentials=not _allow_all,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(chat.router,             prefix="/api/chat",          tags=["chat"])
app.include_router(dashboard.router,        prefix="/api/dashboard",     tags=["dashboard"])
app.include_router(accounts.router,         prefix="/api/accounts",      tags=["accounts"])
app.include_router(inventory.router,        prefix="/api/inventory",     tags=["inventory"])
app.include_router(settings_router.router,  prefix="/api/settings",      tags=["settings"])
app.include_router(webhooks.router,         prefix="/webhooks",          tags=["webhooks"])
app.include_router(actions.router,          prefix="/api/actions",       tags=["actions"])
app.include_router(auth.router,             prefix="/auth",              tags=["auth"])
app.include_router(agent.router,            prefix="/api/agent",         tags=["agent"])
app.include_router(notifications.router,    prefix="/api/notifications", tags=["notifications"])
app.include_router(inbox.router,            prefix="/api/inbox",         tags=["inbox"])
app.include_router(workflows.router,        prefix="/api/workflows",      tags=["workflows"])
app.include_router(feed.router,             prefix="/api/feed",          tags=["feed"])
app.include_router(setup.router,            prefix="/api/setup",         tags=["setup"])
app.include_router(computer_use_router.router, prefix="/api/computer-use", tags=["computer-use"])
app.include_router(billing.router,             prefix="/api/billing",       tags=["billing"])
from app.api import logistics as logistics_router
app.include_router(logistics_router.router,    prefix="/api/logistics",      tags=["logistics"])
from app.api import compliance as compliance_router
app.include_router(compliance_router.router,   prefix="/api/compliance",     tags=["compliance"])
from app.api import connectors as connectors_router
app.include_router(connectors_router.router,   prefix="/api/connectors",     tags=["connectors"])
app.include_router(docs_router.router,         prefix="/api/docs",           tags=["docs"])
from app.api import admin as admin_router
app.include_router(admin_router.router,        prefix="/api/admin",          tags=["admin"])

register_error_handlers(app)


# ── Health check — deep ping ──────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health_check():
    """
    Readiness probe — returns 200 only if DB and Redis are reachable.
    Kubernetes / Docker health checks should hit this endpoint.
    """
    checks: dict[str, str] = {}
    ok = True

    # Redis
    try:
        r = redis_lib.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        # Log the detail server-side only; never leak host/DSN/exception text.
        log.error("health: redis check failed: %s", exc)
        checks["redis"] = "error"
        ok = False

    # Supabase (lightweight table count query)
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        db.table("companies").select("id", count="exact").limit(1).execute()
        checks["db"] = "ok"
    except Exception as exc:
        log.error("health: db check failed: %s", exc)
        checks["db"] = "error"
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "version": APP_VERSION, "checks": checks},
        status_code=status_code,
    )
