"""Per-request X-Request-ID middleware.

A pure ASGI middleware, not ``@app.middleware("http")``. Starlette's
``BaseHTTPMiddleware`` (what that decorator installs) runs the route in a
child task and hands its body to the outer response through an anyio memory
stream. When the client disconnects, or the auth guard ends a revoked
stream (#2816), that child task is cancelled wherever it is waiting, often
in the memory stream's ``send`` while the route's generator is suspended at
``yield``. The generator is then never closed: its ``finally`` (event-bus
unsubscribes, upstream relay closes) waits for the garbage collector. Here
the route sends straight through, so the cancellation lands inside the
generator and its ``finally`` runs at once.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADER = "x-request-id"


class RequestIdMiddleware:
    """Echo or mint ``X-Request-ID`` and bind it to the structlog context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        req_id = Headers(scope=scope).get(_HEADER) or uuid.uuid4().hex

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[_HEADER] = req_id
            await send(message)

        structlog.contextvars.bind_contextvars(request_id=req_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")


def install(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)


__all__ = ["RequestIdMiddleware", "install"]
