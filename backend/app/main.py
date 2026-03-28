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
        before_send=lambda event, hint: _scrub_sentry_event(event),
    )


def _scrub_sentry_event(event: dict) -> dict:
    """Remove sensitive fields before sending to Sentry."""
    for frame in (
        event.get("exception", {})
            .get("values", [{}])[0]
            .get("stacktrace", {})
            .get("frames", [])
    ):
        frame.get("vars", {}).pop("password", None)
        frame.get("vars", {}).pop("secret_key", None)
        frame.get("vars", {}).pop("access_token", None)
    return event


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("SecretaryAI API starting up")
    yield
    log.info("SecretaryAI API shut down")


# ── Docs: served behind API key in production ──────────────────────────────────
app = FastAPI(
    title="SecretaryAI API",
    version="1.0.0",
    description="AI operations manager for importers and distributors",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

from fastapi.openapi.docs import get_swagger_ui_html
from fastapi import Request
from fastapi.responses import HTMLResponse


@app.get("/docs", include_in_schema=False)
async def custom_docs(request: Request):
    """API docs — accessible with X-Docs-Key header (value from DOCS_API_KEY env var)."""
    docs_key = request.headers.get("x-docs-key", "")
    expected = settings.docs_api_key if hasattr(settings, "docs_api_key") else ""
    if not expected or docs_key != expected:
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not authorized. Include X-Docs-Key header."}, status_code=401)
    return get_swagger_ui_html(openapi_url="/openapi.json", title="SecretaryAI API")


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
        checks["redis"] = f"error: {exc}"
        ok = False

    # Supabase (lightweight table count query)
    try:
        from supabase import create_client
        db = create_client(settings.supabase_url, settings.supabase_service_role_key)
        db.table("companies").select("id", count="exact").limit(1).execute()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        ok = False

    status_code = 200 if ok else 503
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "version": "1.0.0", "checks": checks},
        status_code=status_code,
    )
