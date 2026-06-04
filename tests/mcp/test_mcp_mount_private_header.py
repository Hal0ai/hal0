"""Regression test for issue #413 — MCP ``--private`` namespace promotion.

The bug: ``hal0.api.mcp_mount`` parsed ``X-hal0-Private: 1`` into a
contextvar inside a Starlette ``BaseHTTPMiddleware`` (per-request task),
but FastMCP runs tool handlers in a *lifespan-scoped* anyio task group
spun up by ``StreamableHTTPSessionManager.run()`` — not the per-request
task the middleware writes into. The contextvar value never reached the
handler, so ``private_resolver`` always returned ``False`` and every
write collapsed to the ``shared`` namespace regardless of the header.

The fix reads the caller headers straight off the MCP SDK's per-handler
request context (``mcp.server.lowlevel.server.request_ctx``), which the
SDK sets *inside* the handler's own task and stamps with the originating
Starlette ``Request`` (headers and all). These tests reproduce the
in-handler condition directly: we set the SDK ``request_ctx`` to a
``RequestContext`` carrying a real ``Request`` with (or without) the
header, then drive the production resolvers + the mounted memory tool.

On ``main`` the resolvers read the dropped ``_caller`` contextvar, so
``private_resolver()`` returns ``False`` even with the header present —
``test_private_resolver_reads_header_off_request_ctx`` fails there. After
the fix both the resolver and the tool path honour the live header.

The conftest stub only kicks in when the real ``mcp`` SDK is absent;
these tests need the real SDK (the request context only exists in the
real transport) and skip cleanly when it isn't installed.
"""

from __future__ import annotations

from typing import Any

import pytest

# These tests exercise the real MCP request context — skip when the SDK
# isn't installed (the tests/mcp conftest otherwise installs a stub that
# has no request context to read).
pytest.importorskip("mcp.server.fastmcp")
request_ctx_mod = pytest.importorskip("mcp.server.lowlevel.server")

from starlette.requests import Request  # noqa: E402

from hal0.api import mcp_mount  # noqa: E402
from hal0.mcp import memory  # noqa: E402

_request_ctx = request_ctx_mod.request_ctx


def _make_request(headers: dict[str, str]) -> Request:
    """Build a minimal Starlette ``Request`` carrying ``headers``."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/mcp/memory", "headers": raw})


class _CtxToken:
    """Set the SDK ``request_ctx`` to a context whose ``.request`` carries
    the given headers — mirrors what the streamable-HTTP transport does
    inside the handler task (``mcp.server.lowlevel.server``)."""

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers
        self._token: Any = None

    def __enter__(self) -> None:
        from mcp.shared.context import RequestContext

        ctx = RequestContext(
            request_id=1,
            meta=None,
            session=None,
            lifespan_context=None,
            request=_make_request(self._headers),
        )
        self._token = _request_ctx.set(ctx)

    def __exit__(self, *exc: object) -> None:
        _request_ctx.reset(self._token)


class _RecordingWrapper:
    """Stand-in CogneeWrapper that records the ``dataset`` each call gets."""

    def __init__(self) -> None:
        self.add_calls: list[dict[str, Any]] = []

    async def add(
        self,
        *,
        text: str,
        dataset: str,
        tags: list[str],
        source: str,
        metadata: dict[str, Any],
        client_id: str | None = None,
    ) -> dict[str, Any]:
        self.add_calls.append(
            {"text": text, "dataset": dataset, "source": source, "client_id": client_id}
        )
        return {"id": "id-1", "timestamp": "2026-06-04T00:00:00Z"}


def test_private_resolver_reads_header_off_request_ctx() -> None:
    """``private_resolver`` honours X-hal0-Private off the live request.

    Fails on main (reads the dropped ``_caller`` contextvar → False);
    passes once the resolver reads the SDK request context.
    """
    with _CtxToken({"X-hal0-Private": "1"}):
        assert mcp_mount.private_resolver() is True
        assert mcp_mount.client_id_resolver() == "anonymous"
    with _CtxToken({}):
        assert mcp_mount.private_resolver() is False
    # Outside any MCP request context the resolver falls back to False.
    assert mcp_mount.private_resolver() is False


def test_bearer_resolver_stamps_client_id_off_request_ctx() -> None:
    """A Bearer token on the live request becomes the client_id."""
    with _CtxToken({"Authorization": "Bearer pi-coder-token"}):
        bearer, client_id = mcp_mount.bearer_resolver()
        assert bearer == "pi-coder-token"
        assert client_id == "pi-coder-token"


@pytest.mark.asyncio
async def test_memory_add_promotes_to_private_namespace_via_resolvers() -> None:
    """End-to-end: the dispatcher wired with the production resolvers
    promotes a write to ``private:<client_id>`` when the live MCP request
    carries ``X-hal0-Private: 1`` — and stays ``shared`` without it.

    This is the user-visible symptom from #413: private writes silently
    landed in ``shared`` because the resolver never saw the header.
    """
    wrapper = _RecordingWrapper()
    dispatcher = memory.make_dispatcher(
        wrapper,
        client_id_resolver=mcp_mount.client_id_resolver,
        private_resolver=mcp_mount.private_resolver,
    )

    with _CtxToken({"X-hal0-Private": "1"}):
        out = await dispatcher("memory_add", {"text": "remember me", "dataset": ""})
    assert out["status"] == "ok", out
    assert wrapper.add_calls[-1]["dataset"] == "private:anonymous"

    with _CtxToken({}):
        out = await dispatcher("memory_add", {"text": "shared note", "dataset": ""})
    assert out["status"] == "ok", out
    assert wrapper.add_calls[-1]["dataset"] == "shared"
