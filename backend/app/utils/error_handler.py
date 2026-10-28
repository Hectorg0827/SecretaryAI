"""
Unified error handler for FastAPI.
Returns consistent JSON error shapes across all endpoints.
"""
import logging
from types import UnionType
from typing import Annotated, Union, get_args, get_origin
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

_LOCATION_ROOTS = frozenset(("body", "query", "path", "header", "cookie"))
# Only literal, input-independent messages are permitted. Custom error types,
# ValueError text, and Pydantic ctx can contain caller data.
_SAFE_MESSAGES = {
    "missing": "Field required",
    "string_type": "Input should be a valid string",
    "string_too_short": "String is too short",
    "string_too_long": "String is too long",
    "string_pattern_mismatch": "Invalid string format",
    "int_parsing": "Input should be a valid integer",
    "int_type": "Input should be a valid integer",
    "float_parsing": "Input should be a valid number",
    "float_type": "Input should be a valid number",
    "finite_number": "Input should be a finite number",
    "bool_parsing": "Input should be a valid boolean",
    "bool_type": "Input should be a valid boolean",
    "list_type": "Input should be a valid list",
    "dict_type": "Input should be a valid dictionary",
    "json_invalid": "Invalid JSON payload",
    "enum": "Unsupported value",
    "literal_error": "Unsupported value",
    "greater_than": "Value is too small",
    "greater_than_equal": "Value is too small",
    "less_than": "Value is too large",
    "less_than_equal": "Value is too large",
    "too_short": "Not enough items",
    "too_long": "Too many items",
    "value_error": "Invalid value",
}


def _schema_location(request: Request, raw_location: tuple | list) -> list[str | int]:
    """Show declared field paths, never arbitrary request-body dictionary keys.

    Resolve each location segment against the *actual endpoint schema*. An
    integer is a safe list index only when the schema is a list; after any
    dictionary field, both string and integer keys are redacted. Unknown
    paths fail closed instead of relying on a brittle global field-name list.
    """
    if (not raw_location or not isinstance(raw_location[0], str)
            or raw_location[0] not in _LOCATION_ROOTS):
        return ["request"]
    root = raw_location[0]
    path: list[str | int] = [root]
    route = request.scope.get("route")
    dependant = getattr(route, "dependant", None)
    if root == "body":
        field = getattr(route, "body_field", None)
    else:
        fields = getattr(dependant, {"query": "query_params", "path": "path_params",
                                    "header": "header_params", "cookie": "cookie_params"}[root], ())
        field = None
        if len(raw_location) > 1:
            field = next((f for f in fields if raw_location[1] in (f.name, f.alias)), None)
            if field is not None:
                path.append(field.alias)
                raw_location = (root, *raw_location[2:])
    annotation = getattr(getattr(field, "field_info", None), "annotation", None)
    if annotation is None:
        return path

    for part in raw_location[1:]:
        while get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
        origin = get_origin(annotation)
        if origin in (Union, UnionType):
            choices = [a for a in get_args(annotation) if a is not type(None)]
            if len(choices) == 1:
                annotation = choices[0]
            else:
                # Pydantic inserts a static branch name in union-error paths.
                match = next(
                    (a for a in choices
                     if part == str(a).replace("typing.", "").replace(" ", "")),
                    None,
                )
                if match is None:
                    break
                annotation = match
                continue
            origin = get_origin(annotation)

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            model_field = annotation.model_fields.get(part) if isinstance(part, str) else None
            if model_field is None and isinstance(part, str):
                # Error locations can use declared JSON aliases, not Python
                # attribute names. Aliases are schema constants, never keys
                # copied from arbitrary nested dictionaries.
                model_field = next(
                    (f for f in annotation.model_fields.values() if f.alias == part), None
                )
            if model_field is None:
                break
            path.append(part)
            annotation = model_field.annotation
        elif origin in (list, tuple):
            if type(part) is not int or part < 0:
                break
            path.append(part)
            annotation = get_args(annotation)[0] if get_args(annotation) else None
        elif origin is dict:
            path.append("<redacted_key>")
            args = get_args(annotation)
            annotation = args[1] if len(args) == 2 else None
        else:
            break
        if annotation is None:
            break
    return path


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # Never copy input, ctx, msg, or arbitrary loc components from Pydantic.
        safe_errors = []
        for error in exc.errors():
            code = error.get("type", "value_error")
            if code not in _SAFE_MESSAGES:
                code = "value_error"
            safe_errors.append({
                "type": code,
                "loc": _schema_location(request, error.get("loc", ())),
                "msg": _SAFE_MESSAGES[code],
            })
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "validation_error",
                "detail": safe_errors,
                "message": "Request validation failed",
            },
        )

    @app.exception_handler(ValidationError)
    async def internal_validation_error_handler(request: Request, exc: ValidationError):
        # Request schema errors are handled above as 422. Model validation
        # inside a handler is a server defect, not an invalid HTTP request.
        # Pydantic ValidationError subclasses ValueError, whose handler below
        # would otherwise echo str(exc), including submitted secret values.
        log.error("Internal model validation failed at %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred. Our team has been notified.",
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
