"""Per-app binding for the process-wide MCP tool definitions (#2396).

Building a FastMCP tool is expensive: ``Tool.from_function`` inspects the
signature and builds (and JSON-schemas) a pydantic model per tool. With ~209
tools across ``/mcp/admin`` and ``/mcp/memory`` that was ~0.2 s per
``create_app()`` — most of app construction, and ~31% of the test suite's
CPU, because the tools were rebuilt for every app with the app's state
captured in their closures.

The split this module supports:

* **Definitions, once per process.** :mod:`hal0.mcp.admin` and
  :mod:`hal0.mcp.memory` build their :class:`Tool` objects once and share
  them between every server. A definition captures nothing per-app.
* **Bindings, per app, resolved at call time.** Each mount still gets its
  own :class:`BoundFastMCP` (a session manager's ``run()`` is single-use and
  the transport-security settings are per mount), carrying the app's
  ``state``. A tool reaches its server through the FastMCP :class:`Context`
  FastMCP injects into every call, and reads the approval queue, memory
  provider, dispatcher and base URL off ``state`` then — the same
  ``app.state`` lookup the REST routes use. Swapping a field on
  ``app.state`` after the mount therefore takes effect on the next call.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Final

import anyio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.server import Settings as _FastMCPSettings
from mcp.server.fastmcp.tools import Tool

# FastMCP's ``Settings`` is a pydantic-settings model with ``env_file=".env"``,
# so every ``FastMCP(...)`` opened ``.env`` in the process's CURRENT directory.
# hal0 never configures FastMCP through a dotenv file (its settings are its
# own config), and the directory is wherever the caller sits: ``create_app()``
# under ``hal0 agent reprovision hermes --repair`` run from ``/root`` as the
# ``hal0`` user raised ``PermissionError: '.env'`` and failed the whole MCP
# mount (#2907). pydantic-settings reads ``model_config["env_file"]`` at
# instantiation, so clearing it here covers every FastMCP hal0 builds.
_FastMCPSettings.model_config["env_file"] = None

#: The logger name the MCP streamable-HTTP transport uses for standalone SSE streaming.
STREAMABLE_HTTP_LOGGER_NAME: Final[str] = "mcp.server.streamable_http"


def _is_closed_resource_error(exc: Any) -> bool:
    """Return True if *exc* represents an anyio closed resource error."""
    if exc is None:
        return False
    if (
        isinstance(exc, anyio.ClosedResourceError)
        or getattr(exc, "__name__", None) == "ClosedResourceError"
        or exc.__class__.__name__ == "ClosedResourceError"
    ):
        return True
    if isinstance(exc, type) and issubclass(exc, anyio.ClosedResourceError):
        return True
    if hasattr(exc, "exceptions"):
        return any(_is_closed_resource_error(sub) for sub in exc.exceptions)
    return False


class ClosedStreamFilter(logging.Filter):
    """Filter dropping spurious ``ClosedResourceError`` tracebacks on disconnect (#3654).

    When a client disconnects while a standalone GET SSE stream is open,
    ``terminate()`` in ``mcp.server.streamable_http`` closes the memory stream.
    ``standalone_sse_writer`` catches the resulting ``anyio.ClosedResourceError``
    and logs an ERROR with a 44-line Rich traceback. A client disconnect is a
    routine end of session, not a server fault.

    This filter drops those expected records while allowing genuine errors
    (other exceptions or other log messages) to pass through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.msg)
        if record.args:
            with contextlib.suppress(Exception):
                msg = record.getMessage()
        if record.levelno >= logging.ERROR and "standalone SSE writer" in msg and record.exc_info:
            exc = (
                record.exc_info[1]
                if (
                    isinstance(record.exc_info, tuple)
                    and len(record.exc_info) > 1
                    and record.exc_info[1] is not None
                )
                else (
                    record.exc_info[0]
                    if isinstance(record.exc_info, tuple) and len(record.exc_info) > 0
                    else record.exc_info
                )
            )
            if _is_closed_resource_error(exc):
                return False
        return True


def install_closed_stream_filter() -> None:
    """Attach :class:`ClosedStreamFilter` to ``mcp.server.streamable_http``.

    Idempotent: if an instance of :class:`ClosedStreamFilter` is already
    attached to the logger, it is not attached again.
    """
    logger = logging.getLogger(STREAMABLE_HTTP_LOGGER_NAME)
    if not any(isinstance(existing, ClosedStreamFilter) for existing in logger.filters):
        logger.addFilter(ClosedStreamFilter())


# Install the filter at import time so every FastMCP server process is covered.
install_closed_stream_filter()

# The annotation a shared tool gives its injected-context parameter. It must
# stay the BARE class: FastMCP finds the context parameter with
# ``inspect.isclass(...)`` on the (optionally ``| None``) annotation, so a
# parametrised ``Context[...]`` alias would go undetected and the parameter
# would be advertised as a tool argument instead of injected. A plain
# assignment, not a ``type`` statement: that builds a ``TypeAliasType`` the
# same ``isclass`` check would not see through either. Type checkers get
# the fully-parametrised spelling.
if TYPE_CHECKING:
    ToolContext = Context[Any, Any, Any]
else:
    ToolContext = Context


class BoundFastMCP(FastMCP):
    """A FastMCP server whose shared tools resolve per-app state from it.

    ``state`` is ``app.state`` for a mounted server, or a namespace with the
    same attribute names for a server built directly (unit tests, the CLI).
    ``hooks`` holds the transport-layer callables (caller identity, private
    mode) — process-wide functions, not app state, so they live on the server
    rather than on ``state``.
    """

    def __init__(
        self,
        name: str,
        *,
        tools: Iterable[Tool],
        state: Any,
        hooks: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(name, tools=list(tools))
        self.hal0_state = state
        self.hal0_hooks: dict[str, Any] = dict(hooks or {})
        # FastMCP's constructor has no ``version`` kwarg, so the underlying
        # ``mcp.server.lowlevel.Server`` defaults ``initialize``'s
        # ``serverInfo.version`` to the ``mcp`` SDK package version — a client
        # would see the library version, not hal0's own (#1796).
        from hal0 import __version__ as _hal0_version

        self._mcp_server.version = _hal0_version
        install_closed_stream_filter()


def bound_server(ctx: ToolContext | None) -> BoundFastMCP:
    """Return the :class:`BoundFastMCP` handling the current tool call.

    Raises :class:`ToolError` when the call carries no hal0 server — a shared
    definition run with no context must fail loudly rather than fall back to
    some other app's state.
    """
    server: Any = None
    if ctx is not None:
        try:
            server = ctx.fastmcp
        except ValueError:
            server = None
    if not isinstance(server, BoundFastMCP):
        raise ToolError(
            "MCP tool is not bound to a hal0 MCP server — invoke it through the "
            "server (FastMCP.call_tool), which supplies the per-app state"
        )
    return server


__all__ = [
    "STREAMABLE_HTTP_LOGGER_NAME",
    "BoundFastMCP",
    "ClosedStreamFilter",
    "ToolContext",
    "bound_server",
    "install_closed_stream_filter",
]
