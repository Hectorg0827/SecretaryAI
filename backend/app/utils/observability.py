"""
Structured logging helpers for request-scoped and task-scoped observability.

Provides:
  - `bind_request_context(request)` — returns a logger adapter that pre-fills
    request_id and company_id on every log record.
  - `log_duration(logger, label)` — context manager that measures elapsed time
    and emits a structured timing record on exit.
  - `task_logger(task_name, company_id)` — returns a logger adapter for Celery
    tasks with task_name and company_id bound.

Usage in a route handler:
    from app.utils.observability import bind_request_context, log_duration

    @router.get("/my-endpoint")
    async def handler(request: Request):
        log = bind_request_context(request)
        with log_duration(log, "db_query"):
            rows = await db.fetch(...)
        log.info("handler complete", extra={"row_count": len(rows)})

Usage in a Celery task:
    from app.utils.observability import task_logger

    @celery.task
    def my_task(company_id):
        log = task_logger("my_task", company_id)
        log.info("task started")
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

from starlette.requests import Request


class _BoundLogger(logging.LoggerAdapter):
    """Logger adapter that merges pre-bound extras into every log call."""

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return msg, kwargs


def bind_request_context(request: Request, logger_name: str = "app") -> _BoundLogger:
    """
    Return a logger adapter pre-populated with request_id and company_id
    extracted from `request.state`.
    """
    base = logging.getLogger(logger_name)
    request_id = getattr(request.state, "request_id", None) or "-"
    company_id  = getattr(request.state, "company_id",  None) or "-"
    return _BoundLogger(base, {"request_id": request_id, "company_id": company_id})


def task_logger(task_name: str, company_id: str | None = None) -> _BoundLogger:
    """
    Return a logger adapter pre-populated with task_name and company_id for
    use inside Celery tasks.
    """
    base = logging.getLogger(f"tasks.{task_name}")
    return _BoundLogger(base, {
        "task_name": task_name,
        "company_id": company_id or "-",
    })


@contextmanager
def log_duration(
    logger: logging.Logger | _BoundLogger,
    label: str,
    extra: dict[str, Any] | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that measures wall-clock duration and emits a structured
    ``timing`` record on exit.

    Example output (as JSON with python-json-logger):
      {"level": "info", "event": "timing", "label": "db_query", "duration_ms": 42.3}
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        merged = {"label": label, "duration_ms": duration_ms, **(extra or {})}
        logger.info("timing", extra=merged)
