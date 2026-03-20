import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.config import get_settings
from app.api import chat, dashboard, accounts, inventory, settings as settings_router, webhooks, actions, auth, agent, notifications
from app.utils.error_handler import register_error_handlers

settings = get_settings()

if settings.sentry_dsn:
    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    yield
    # Shutdown


app = FastAPI(
    title="SecretaryAI API",
    version="1.0.0",
    description="AI operations manager for importers and distributors",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
_allow_all = "*" in _cors_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_all else _cors_origins,
    allow_credentials=not _allow_all,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(inventory.router, prefix="/api/inventory", tags=["inventory"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(actions.router, prefix="/api/actions", tags=["actions"])
app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(agent.router, prefix="/api/agent", tags=["agent"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])

register_error_handlers(app)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}
