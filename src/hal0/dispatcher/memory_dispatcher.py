"""In-process memory dispatcher for the MCP admin server.

ADR-0004 §7 plus ADR-0005 §2 together demand: the admin MCP server's
``memory_*`` tool family must reach the memory provider *without* an
HTTP loop-back through ``/mcp/memory``. The loop-back works (and was the
v0.2 stopgap), but every round trip pays a transport tax and re-runs
Bearer verification against the local API just to land in the same
Python process.

:class:`MemoryDispatcher` is the thin adapter that closes the loop. It
holds a memory provider (:class:`~hal0.memory.MemoryProvider`), wires the
same per-call ``client_id`` / ``private`` resolvers the
out-of-process server uses, and exposes the callable shape that
:func:`hal0.mcp.admin.dispatch` expects on its ``memory_dispatcher=``
kwarg::

    async def(tool: str, args: dict) -> dict

Under the hood we delegate to :func:`hal0.mcp.memory.make_dispatcher`
so validation, namespace promotion, and error envelopes stay
single-sourced. The class wrapper exists so the orchestrator wires up
ONE object (testable, mockable, swappable) instead of a free-floating
closure.

Construction is cheap and synchronous — the orchestrator instantiates
in ``create_app`` next to the memory provider itself, then passes the
instance into ``mount_mcp_servers(...)`` as ``memory_dispatcher=``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from hal0.mcp.memory import make_dispatcher

log = structlog.get_logger(__name__)


class MemoryDispatcher:
    """In-process bridge from MCP admin to a memory provider.

    Parameters
    ----------
    wrapper:
        The memory provider (:class:`hal0.memory.MemoryProvider`) the
        dispatcher should call. Pass the same singleton the memory MCP
        server holds so both transports see one memory-provider state.
    client_id_resolver:
        Zero-arg callable returning the caller id (from the
        ``X-hal0-Agent`` header) for the current request. Defaults to
        "anonymous" — tests that don't care about audit grounding can
        leave this None.
    private_resolver:
        Zero-arg callable returning whether the current call opted into
        the ``private:<client_id>`` namespace (ADR-0005 §3). Defaults
        to False.

    The instance is callable so existing call sites in
    :mod:`hal0.mcp.admin` that expect a plain ``Callable[[str, dict], Awaitable[dict]]``
    keep working without a shim. ``dispatch`` is the explicit method
    for callers that want to be obvious about the call shape.
    """

    def __init__(
        self,
        wrapper: Any,
        *,
        client_id_resolver: Callable[[], str] | None = None,
        private_resolver: Callable[[], bool] | None = None,
    ) -> None:
        self._wrapper = wrapper
        self._client_id_resolver = client_id_resolver
        self._private_resolver = private_resolver
        # Build the underlying closure once. Re-binding per-call would
        # waste work — make_dispatcher's closure already reads the
        # resolvers at call time.
        self._inner = make_dispatcher(
            wrapper,
            client_id_resolver=client_id_resolver,
            private_resolver=private_resolver,
        )

    async def dispatch(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Run one ``memory_*`` tool. Returns the JSON envelope shape the
        MCP admin server forwards verbatim to the client.

        Errors are caught inside :func:`hal0.mcp.memory.make_dispatcher`
        and serialised as ``{status: "error", error: {...}}`` payloads;
        callers do not need to wrap this call in ``try/except``.
        """
        return await self._inner(tool, args)

    async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Callable sugar — same as :meth:`dispatch`."""
        return await self._inner(tool, args)

    @property
    def wrapper(self) -> Any:
        """The underlying memory provider. Exposed so tests can introspect
        which instance the dispatcher is bound to without reaching into
        a private attribute."""
        return self._wrapper


__all__ = ["MemoryDispatcher"]
