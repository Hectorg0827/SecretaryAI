"""
Unified error handler for FastAPI.
Returns consistent JSON error shapes across all endpoints.
"""
import logging
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

log = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # Pydantic errors may contain submitted secrets in `input`, `ctx`,
        # `msg`, or even dynamic dictionary-key components of `loc`. Only
        # expose a known location category and a static message; never echo
        # any caller-controlled portion of a validation error.
        safe_errors = []
        for error in exc.errors():
            loc = error.get("loc", ())
            root = loc[0] if loc else None
            if root not in ("body", "query", "path", "header", "cookie"):
                root = "request"
            safe_errors.append({
                "type": "value_error",
                "loc": [root],
                "msg": "Invalid request data",
            })
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "validation_error",
                "detail": safe_errors,
                "message": "Request validation failed",
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        log.warning("ValueError at %s: %s", request.url, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "bad_request", "message": str(exc)},
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, exc: PermissionError):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"error": "forbidden", "message": str(exc)},
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception):
        log.error("Unhandled error at %s: %s", request.url, exc, exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred. Our team has been notified.",
            },
        )
