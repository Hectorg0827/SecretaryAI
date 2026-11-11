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
        # Build the response by hand:
        #  * `ctx` can hold the raw exception object (a custom validator's
        #    ValueError), which is not JSON-serialisable — that turned every
        #    such validation failure (e.g. a weak password at sign-up) into a
        #    500 error;
        #  * `input` echoes what the user sent, which may be a password.
        details = []
        for err in exc.errors():
            msg = str(err.get("msg", "Invalid value"))
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            details.append({
                "loc": [str(p) for p in err.get("loc", ())],
                "msg": msg,
                "type": str(err.get("type", "value_error")),
            })
        first = details[0]["msg"] if details else "Request validation failed"
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "validation_error",
                "detail": details,
                # One plain sentence the UI can show as-is.
                "message": first,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        # The text of an unexpected ValueError can contain internal detail
        # (SQL, file paths, upstream responses): log it, do not return it.
        log.warning("ValueError at %s: %s", request.url, exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "bad_request", "message": "The request could not be processed."},
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
