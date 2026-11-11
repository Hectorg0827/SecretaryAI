"""Staging-only, owner-approved Microsoft mailbox OAuth.

Company identity is derived exclusively from a one-use Redis state, not from
Microsoft callback query parameters. Uses PKCE, a server-side client secret,
and a conditional company update to avoid reviving disconnected credentials.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import logging
import secrets
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.api.auth import _state_pop, _state_set
from app.auth.rbac import require_permission
from app.config import get_settings
from app.utils.encryption import encrypt

router = APIRouter()
log = logging.getLogger(__name__)
_SCOPES = "offline_access User.Read Mail.ReadWrite Mail.Send"
_AUTHORIZE = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
_TOKEN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_GRAPH_ME = "https://graph.microsoft.com/v1.0/me"


def _settings():
    settings = get_settings()
    if settings.secretaryai_deployment_env.strip().lower() != "staging":
        raise HTTPException(status_code=404, detail="Not found")
    if not all((settings.ms_client_id, settings.ms_client_secret, settings.ms_redirect_uri)):
        raise HTTPException(status_code=503, detail="Outlook sign-in not configured")
    redirect = urlsplit(settings.ms_redirect_uri)
    if (redirect.scheme != "https" or redirect.hostname != "secretaryai-backend-staging.up.railway.app"
            or redirect.path != "/auth/outlook/callback" or redirect.port is not None
            or redirect.username or redirect.password or redirect.query or redirect.fragment):
        raise HTTPException(status_code=503, detail="Outlook callback misconfigured")
    return settings


def _frontend_url(settings) -> str:
    target = urlsplit(settings.frontend_url)
    if (target.scheme != "https" or target.hostname != "secretaryai-web-staging.up.railway.app"
            or target.port is not None or target.username or target.password
            or target.query or target.fragment):
        raise HTTPException(status_code=503, detail="Outlook return URL misconfigured")
    return "https://secretaryai-web-staging.up.railway.app/settings"


def _db():
    from app.api.deps import get_db
    return get_db()


def _company_row(company_id: str) -> dict:
    try:
        rows = _db().table("companies").select("id,ms_access_token").eq(
            "id", company_id
        ).limit(2).execute().data
    except Exception:
        raise HTTPException(status_code=503, detail="Outlook company lookup unavailable") from None
    if len(rows or []) != 1 or rows[0].get("id") != company_id:
        raise HTTPException(status_code=404, detail="Company not found")
    return rows[0]


def _active_owner(user_id: str, company_id: str) -> None:
    from app.api.team import _owner_record
    _owner_record(_db(), {"sub": user_id, "company_id": company_id, "role": "owner"})


def _require_audit(company_id: str, actor_id: str, event: str) -> None:
    from app.utils.audit import write_audit_event
    try:
        result = write_audit_event(
            _db(), company_id=company_id, event_type=event,
            actor_id=actor_id, action_class="commit", capability="outlook_connection",
            path_used="api", metadata={"gate": "staging_outlook"},
        )
    except Exception:
        result = None
    if not result:
        raise HTTPException(status_code=503, detail="Outlook connection audit unavailable")


def _token_fingerprint(token: str | None) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


@contextmanager
def _company_lock(company_id: str):
    """Serialize consent completion/disconnect across API workers."""
    import redis
    try:
        cache = redis.from_url(get_settings().redis_url, decode_responses=True)
        lock = cache.lock(f"outlook_connection_lock:{company_id}", timeout=30, blocking_timeout=5)
        if not lock.acquire(blocking=True):
            raise RuntimeError("Outlook connection busy")
    except Exception:
        raise HTTPException(status_code=503, detail="Outlook connection lock unavailable") from None
    try:
        yield cache
    finally:
        try:
            lock.release()
        except Exception:
            log.error("Outlook connection lock release unavailable")


def _starting_generation(company_id: str) -> tuple[str, str]:
    with _company_lock(company_id) as cache:
        row = _company_row(company_id)
        return (_token_fingerprint(row.get("ms_access_token")),
                cache.get(f"outlook_connection_epoch:{company_id}") or "0")


def _claim_state(raw: str) -> dict:
    try:
        data = json.loads(_state_pop(f"ms:{raw}") or "null")
        if (not isinstance(data, dict) or not all(
            isinstance(data.get(key), str) and data[key]
            for key in ("company_id", "actor_id", "version", "pkce", "epoch")
        )):
            raise ValueError("Malformed OAuth state")
        return data
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state") from None


def _write_tokens(company_id: str, actor_id: str, expected_version: str,
                  expected_epoch: str, tokens: dict, settings) -> None:
    # Read and compare before the conditional update. Disconnect/reconnect
    # while the Microsoft dialog is open makes a prior callback stale.
    with _company_lock(company_id) as cache:
        _active_owner(actor_id, company_id)
        current_epoch = cache.get(f"outlook_connection_epoch:{company_id}") or "0"
        original = _company_row(company_id).get("ms_access_token")
        if current_epoch != expected_epoch or _token_fingerprint(original) != expected_version:
            raise HTTPException(status_code=409, detail="Outlook connection changed; start again")
        updates = {
            "ms_tenant_id": settings.ms_tenant,
            "ms_access_token": encrypt(tokens["access_token"], settings.secret_key),
            "ms_refresh_token": encrypt(tokens["refresh_token"], settings.secret_key),
        }
        query = _db().table("companies").update(updates).eq("id", company_id)
        query = query.eq("ms_access_token", original) if original else query.is_("ms_access_token", "null")
        try:
            rows = query.execute().data
        except Exception:
            raise HTTPException(status_code=503, detail="Outlook connection storage unavailable") from None
        if len(rows or []) != 1:
            raise HTTPException(status_code=409, detail="Outlook connection changed; start again")


def _clear_tokens(company_id: str, actor_id: str) -> None:
    with _company_lock(company_id) as cache:
        _active_owner(actor_id, company_id)
        # Bump even for a disconnected mailbox: an in-flight first-time consent
        # must not reconnect a mailbox after an explicit disconnect request.
        try:
            cache.incr(f"outlook_connection_epoch:{company_id}")
        except Exception:
            raise HTTPException(status_code=503, detail="Outlook revocation unavailable") from None
        try:
            result = _db().table("companies").update({
                "ms_access_token": None, "ms_refresh_token": None, "ms_tenant_id": None,
            }).eq("id", company_id).execute()
        except Exception:
            raise HTTPException(status_code=503, detail="Outlook disconnect temporarily unavailable") from None
        if len(getattr(result, "data", None) or []) != 1:
            raise HTTPException(status_code=503, detail="Outlook company unavailable")


async def _exchange_code(code: str, pkce: str, settings) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(_TOKEN.format(tenant=settings.ms_tenant), data={
            "grant_type": "authorization_code", "code": code,
            "client_id": settings.ms_client_id, "client_secret": settings.ms_client_secret,
            "redirect_uri": settings.ms_redirect_uri, "scope": _SCOPES,
            "code_verifier": pkce,
        })
        response.raise_for_status()
        tokens = response.json()
        if not isinstance(tokens, dict) or not all(
            isinstance(tokens.get(k), str) and tokens[k] for k in ("access_token", "refresh_token")
        ):
            raise ValueError("Microsoft did not return offline credentials")
        me = await client.get(_GRAPH_ME, headers={
            "Authorization": f"Bearer {tokens['access_token']}",
        }, params={"$select": "id,userPrincipalName,mail"})
        me.raise_for_status()
        identity = me.json()
        if not isinstance(identity, dict) or not identity.get("id"):
            raise ValueError("Microsoft mailbox identity could not be verified")
        return tokens


@router.get("/connect-url")
async def outlook_connect_url(user: dict = Depends(require_permission("manage_users"))):
    settings = _settings()
    _active_owner(user["sub"], user["company_id"])
    version, epoch = await run_in_threadpool(_starting_generation, user["company_id"])
    state = secrets.token_urlsafe(32)
    pkce = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(pkce.encode()).digest()).rstrip(b"=").decode()
    _state_set(f"ms:{state}", json.dumps({
        "company_id": user["company_id"], "actor_id": user["sub"],
        "version": version, "epoch": epoch, "pkce": pkce,
    }))
    params = {
        "client_id": settings.ms_client_id, "response_type": "code",
        "redirect_uri": settings.ms_redirect_uri, "response_mode": "query",
        "scope": _SCOPES, "state": state, "prompt": "select_account",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return {"url": f"{_AUTHORIZE.format(tenant=settings.ms_tenant)}?{urlencode(params)}"}


@router.get("/callback")
async def outlook_callback(
    code: str = Query(default="", max_length=4096),
    state: str = Query(default="", max_length=300),
    error: str = Query(default="", max_length=200),
):
    settings = _settings()
    return_url = _frontend_url(settings)
    # Even provider-declined flows must consume the state to prevent reuse.
    data = _claim_state(state)
    if error:
        return RedirectResponse(url=f"{return_url}?outlook=error")
    if not code:
        raise HTTPException(status_code=400, detail="Missing Microsoft authorization code")
    await run_in_threadpool(_active_owner, data["actor_id"], data["company_id"])
    try:
        tokens = await _exchange_code(code, data["pkce"], settings)
    except Exception:
        log.warning("Microsoft authorization or identity verification failed")
        return RedirectResponse(url=f"{return_url}?outlook=error")
    _require_audit(data["company_id"], data["actor_id"], "outlook_connection_requested")
    try:
        await run_in_threadpool(_write_tokens, data["company_id"], data["actor_id"],
                                data["version"], data["epoch"], tokens, settings)
    except HTTPException:
        log.warning("Outlook connection state changed or write failed")
        return RedirectResponse(url=f"{return_url}?outlook=error")
    return RedirectResponse(url=f"{return_url}?outlook=connected")


@router.delete("/disconnect")
async def outlook_disconnect(user: dict = Depends(require_permission("manage_users"))):
    _settings()
    await run_in_threadpool(_active_owner, user["sub"], user["company_id"])
    _require_audit(user["company_id"], user["sub"], "outlook_disconnect_requested")
    await run_in_threadpool(_clear_tokens, user["company_id"], user["sub"])
    return {"status": "disconnected"}
