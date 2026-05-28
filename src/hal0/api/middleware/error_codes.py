"""Structured error envelope.

All non-2xx responses follow:

    {"error": {"code": "<namespace>.<reason>", "message": "...", "details": {...}}}

Error code namespaces: slot.*, model.*, dispatch.*, config.*, system.*,
auth.*, validation.*, resource.*

This middleware catches uncaught exceptions, unhandled HTTPExceptions, and
FastAPI's pydantic ``RequestValidationError`` and reshapes their JSON body.
Handlers that raise typed :class:`Hal0Error` subclasses get their ``code``
and ``details`` propagated as-is.

The typed 4xx subclasses (``BadRequest``, ``Unauthorized``, ``Forbidden``,
``NotFound``, ``Conflict``, ``UnprocessableEntity``) live in
:mod:`hal0.errors` and are re-exported here for the import path most
routes already use.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from hal0.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    Hal0Error,
    NotFound,
    Unauthorized,
    UnprocessableEntity,
)

log = structlog.get_logger(__name__)


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _shape_validation_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Reshape pydantic's per-field error dicts into a stable envelope-friendly form.

    Pydantic emits a list of ``{type, loc, msg, input, ctx, url}`` dicts.
    The envelope contract keeps ``loc`` (so the client knows which field
    failed), ``msg`` (human-readable), and ``type`` (machine-readable
    error kind) — and drops the rest. ``input`` in particular is
    deliberately stripped: it can be verbose, and for body validation
    failures it often contains the caller-supplied payload, which can
    leak secrets if echoed back. The full pydantic payload remains
    accessible via structured logs for server-side debugging.
    """
    shaped: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        shaped.append(
            {
                "loc": list(loc),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return shaped


def install(app: FastAPI) -> None:
    @app.exception_handler(Hal0Error)
    async def _hal0_handler(_: Request, exc: Hal0Error) -> JSONResponse:
        log.warning("hal0.error", code=exc.code, message=exc.message, **exc.details)
        # Honor a ``retry_after_s`` hint in details by promoting it to the
        # ``Retry-After`` HTTP header so OpenAI-compatible SDKs back off
        # correctly.  Only applied on 503 responses (RFC 7231 §7.1.3).
        headers: dict[str, str] | None = None
        retry_after = exc.details.get("retry_after_s") if exc.status == 503 else None
        if isinstance(retry_after, (int, float)) and retry_after > 0:
            headers = {"Retry-After": str(int(retry_after))}
        return JSONResponse(
            status_code=exc.status,
            content=_envelope(exc.code, exc.message, exc.details),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's pydantic validator fires for missing/invalid query, path,
        # and body parameters. Without this handler clients see FastAPI's
        # default ``{"detail": [...]}`` shape, which doesn't match the hal0
        # envelope contract. We reshape into the canonical envelope and
        # keep the FastAPI-default 422 status — OpenAI/FastAPI clients
        # already expect 422 on request-validation failures, so changing
        # the status code would break ergonomic detection at the client.
        fields = _shape_validation_errors(exc)
        log.info(
            "hal0.validation_error",
            path=request.url.path,
            method=request.method,
            fields=fields,
        )
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation.invalid",
                "request validation failed",
                {"fields": fields},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Map FastAPI HTTPException to envelope. Code derives from status.
        code = f"system.http_{exc.status_code}"
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        log.exception("hal0.unhandled", error=str(exc))
        return JSONResponse(
            status_code=500,
            content=_envelope("system.internal", "internal server error"),
        )


__all__ = [
    "BadRequest",
    "Conflict",
    "Forbidden",
    "Hal0Error",
    "NotFound",
    "Unauthorized",
    "UnprocessableEntity",
    "install",
]
