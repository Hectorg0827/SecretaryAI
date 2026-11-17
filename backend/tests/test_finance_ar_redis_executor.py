"""Staging AR cooldown executor isolation — no actual Redis or network.

A delayed DNS lookup can block longer than redis-py socket timeouts. These
tests model that non-cancellable blocking behavior and prove preview cooldown
work cannot exhaust the shared authentication/default executor.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore, Event, Lock

import pytest

from app.api import finance_ar_preview as preview


@pytest.mark.asyncio
async def test_cancelled_dns_lookup_retains_capacity_until_worker_actually_exits(
    monkeypatch,
):
    started = Event()
    release = Event()
    lock = Lock()
    count = 0

    def slow_dns_resolution(*, company_id):
        nonlocal count
        assert company_id == "synthetic-tenant"
        with lock:
            count += 1
            if count >= 2:
                started.set()
        # Simulate a DNS resolver that ignores connect/read socket timeouts.
        release.wait(timeout=3)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        monkeypatch.setattr(preview, "_AR_REDIS_EXECUTOR", pool)
        monkeypatch.setattr(preview, "_AR_REDIS_CAPACITY", BoundedSemaphore(2))
        monkeypatch.setattr(
            preview, "claim_qbo_ar_preview_window", slow_dns_resolution,
        )
        try:
            first = preview._submit_ar_preview_cooldown(
                company_id="synthetic-tenant",
            )
            second = preview._submit_ar_preview_cooldown(
                company_id="synthetic-tenant",
            )
            assert started.wait(timeout=2)

            # A timeout/cancel of the FastAPI await cannot kill getaddrinfo.
            wrapped = asyncio.wrap_future(first)
            wrapped.cancel()
            await asyncio.sleep(0)

            # Crucially, no third task enters any executor queue or the
            # default pool while both dedicated threads remain stalled.
            with pytest.raises(RuntimeError, match="capacity unavailable"):
                preview._submit_ar_preview_cooldown(
                    company_id="synthetic-tenant",
                )
        finally:
            release.set()

        assert first.result(timeout=3) is True
        assert second.result(timeout=3) is True
        # A completed worker frees a slot, including after cancellation.
        third = preview._submit_ar_preview_cooldown(
            company_id="synthetic-tenant",
        )
        assert third.result(timeout=3) is True


def test_rejected_executor_submission_releases_slot(monkeypatch):
    class RejectingExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("executor stopped")

    slots = BoundedSemaphore(1)
    monkeypatch.setattr(preview, "_AR_REDIS_EXECUTOR", RejectingExecutor())
    monkeypatch.setattr(preview, "_AR_REDIS_CAPACITY", slots)

    with pytest.raises(RuntimeError, match="executor stopped"):
        preview._submit_ar_preview_cooldown(company_id="synthetic-tenant")
    assert slots.acquire(blocking=False)
    slots.release()


def test_failed_blocking_worker_also_returns_capacity(monkeypatch):
    slots = BoundedSemaphore(1)

    def failing_redis(*, company_id):
        raise ConnectionError("SYNTHETIC_PRIVATE_REDIS_ERROR")

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(preview, "_AR_REDIS_EXECUTOR", pool)
        monkeypatch.setattr(preview, "_AR_REDIS_CAPACITY", slots)
        monkeypatch.setattr(
            preview, "claim_qbo_ar_preview_window", failing_redis,
        )
        first = preview._submit_ar_preview_cooldown(
            company_id="synthetic-tenant",
        )
        with pytest.raises(ConnectionError):
            first.result(timeout=2)

        monkeypatch.setattr(
            preview, "claim_qbo_ar_preview_window",
            lambda *, company_id: True,
        )
        second = preview._submit_ar_preview_cooldown(
            company_id="synthetic-tenant",
        )
        assert second.result(timeout=2) is True
