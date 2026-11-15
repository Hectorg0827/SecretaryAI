"""Cross-process QBO OAuth refresh coordination; never emit credential material."""
import asyncio
import contextlib
import secrets
import time

import redis
import redis.asyncio as async_redis
from redis.exceptions import WatchError

from app.utils.encryption import decrypt
from app.utils.qbo_token_store import (
    QBOInvalidGrant, QBOReconnectRequired, create_qbo_client,
    mark_qbo_needs_reconnect, persist_rotated_qbo_tokens,
)

# A refresh can spend 10s on the read, 15s at Intuit, and 10s on CAS.
# Keep the lease above that upper bound so a waiter cannot refresh mid-write.
_LOCK_TTL = 45
_LOCK_WAIT = 10


def _key(company_id: str) -> str:
    if not company_id:
        raise RuntimeError("QBO refresh requires a company identifier")
    return f"qbo_refresh:{company_id}"


@contextlib.asynccontextmanager
async def async_qbo_refresh_lock(company_id: str, redis_url: str):
    """Bounded, ownership-checked Redis lease shared by web and task workers."""
    client = async_redis.from_url(redis_url, decode_responses=True)
    key = _key(company_id)
    owner = secrets.token_hex(16)
    acquired = False
    try:
        deadline = asyncio.get_running_loop().time() + _LOCK_WAIT
        while not acquired:
            acquired = bool(await client.set(key, owner, nx=True, ex=_LOCK_TTL))
            if acquired:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError("QuickBooks refresh is busy; retry the request")
            await asyncio.sleep(0.1)
        yield
    finally:
        if acquired:
            async with client.pipeline(transaction=True) as pipe:
                while True:
                    try:
                        await pipe.watch(key)
                        if await pipe.get(key) == owner:
                            pipe.multi()
                            pipe.delete(key)
                            await pipe.execute()
                        break
                    except WatchError:
                        continue
                    finally:
                        await pipe.reset()
        await client.aclose()


@contextlib.contextmanager
def sync_qbo_refresh_lock(company_id: str, redis_url: str):
    """Identical lease semantics for Celery's synchronous refresh code."""
    client = redis.from_url(redis_url, decode_responses=True)
    key = _key(company_id)
    owner = secrets.token_hex(16)
    acquired = False
    try:
        deadline = time.monotonic() + _LOCK_WAIT
        while not acquired:
            acquired = bool(client.set(key, owner, nx=True, ex=_LOCK_TTL))
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("QuickBooks refresh is busy; retry the request")
            time.sleep(0.1)
        yield
    finally:
        if acquired:
            with client.pipeline(transaction=True) as pipe:
                while True:
                    try:
                        pipe.watch(key)
                        if pipe.get(key) == owner:
                            pipe.multi()
                            pipe.delete(key)
                            pipe.execute()
                        break
                    except WatchError:
                        continue
                    finally:
                        pipe.reset()
        client.close()


def read_current_qbo(db, company_id: str, realm_id: str | None) -> dict:
    """Read the source-of-truth generation after acquiring the per-company lease."""
    result = (
        db.table("companies")
        .select("qbo_access_token,qbo_refresh_token,qbo_realm_id,qb_connection_status,qb_type,qbo_token_expires_at")
        .eq("id", company_id)
        .execute()
    )
    rows = getattr(result, "data", None) or []
    if len(rows) != 1:
        raise QBOReconnectRequired("QuickBooks needs to be reconnected")
    row = rows[0]
    if (row.get("qb_connection_status") != "connected"
            or row.get("qb_type") != "online"
            or (realm_id and row.get("qbo_realm_id") != realm_id)
            or not row.get("qbo_access_token")
            or not row.get("qbo_refresh_token")):
        raise QBOReconnectRequired("QuickBooks needs to be reconnected")
    return row


def _adopt(row: dict, cfg: dict, secret_key: str) -> tuple[str, str]:
    """Decrypt only a verified connected row, never a token supplied by the caller."""
    access = decrypt(row["qbo_access_token"], secret_key)
    refresh = decrypt(row["qbo_refresh_token"], secret_key)
    cfg["_qbo_expected_refresh_ciphertext"] = row["qbo_refresh_token"]
    cfg["qbo_access_token"] = access
    cfg["qbo_refresh_token"] = refresh
    return access, refresh


async def refresh_qbo_web(cfg: dict, request_tokens, persist_tokens) -> tuple[str, str]:
    """Web 401 path: lock, detect winner, or rotate/persist once."""
    from starlette.concurrency import run_in_threadpool
    from app.config import get_settings

    settings = get_settings()
    company_id = cfg["id"]
    async with async_qbo_refresh_lock(company_id, settings.redis_url):
        row = await run_in_threadpool(
            lambda: read_current_qbo(create_qbo_client(settings), company_id, cfg.get("qbo_realm_id"))
        )
        original = cfg.get("_qbo_expected_refresh_ciphertext")
        if row["qbo_refresh_token"] != original:
            return _adopt(row, cfg, settings.secret_key)
        try:
            tokens = await request_tokens()
        except QBOInvalidGrant:
            await run_in_threadpool(
                lambda: mark_qbo_needs_reconnect(
                    create_qbo_client(settings),
                    company_id=company_id,
                    expected_refresh_ciphertext=original,
                    realm_id=cfg.get("qbo_realm_id"),
                )
            )
            raise QBOReconnectRequired("QuickBooks needs to be reconnected") from None

        # The callback persists via CAS; never expose uncommitted credentials.
        await persist_tokens(tokens)
        cfg["qbo_access_token"] = tokens["access_token"]
        cfg["qbo_refresh_token"] = tokens["refresh_token"]
        return tokens["access_token"], tokens["refresh_token"]


def refresh_qbo_task(company: dict, cfg: dict, db, token_request) -> bool:
    """Proactive worker path, sharing the exact same generation fence and lock."""
    from app.config import get_settings

    settings = get_settings()
    company_id = company["id"]
    with sync_qbo_refresh_lock(company_id, settings.redis_url):
        row = read_current_qbo(db, company_id, company.get("qbo_realm_id"))
        if row["qbo_refresh_token"] != company["qbo_refresh_token"]:
            _adopt(row, cfg, settings.secret_key)
            return False

        # Another worker may have refreshed while this task waited on the lease.
        # Keep the proactive policy's current threshold outside this helper.
        try:
            new_tokens = asyncio.run(token_request(cfg["qbo_refresh_token"]))
        except QBOInvalidGrant:
            mark_qbo_needs_reconnect(
                db, company_id=company_id,
                expected_refresh_ciphertext=row["qbo_refresh_token"],
                realm_id=company.get("qbo_realm_id"),
            )
            raise QBOReconnectRequired("QuickBooks needs to be reconnected") from None

        new_ciphertext = persist_rotated_qbo_tokens(
            db, company_id=company_id,
            expected_refresh_ciphertext=row["qbo_refresh_token"],
            realm_id=company.get("qbo_realm_id"),
            access_token=new_tokens["access_token"],
            refresh_token=new_tokens["refresh_token"],
            expires_in=new_tokens.get("expires_in", 3600),
            secret_key=settings.secret_key,
        )
        cfg["qbo_access_token"] = new_tokens["access_token"]
        cfg["qbo_refresh_token"] = new_tokens["refresh_token"]
        cfg["_qbo_expected_refresh_ciphertext"] = new_ciphertext
        return True
