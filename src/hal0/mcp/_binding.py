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

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

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


__all__ = ["BoundFastMCP", "ToolContext", "bound_server"]
