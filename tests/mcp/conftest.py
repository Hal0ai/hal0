"""Test fixtures for the hal0.mcp package.

The MCP server modules (:mod:`hal0.mcp.admin`, :mod:`hal0.mcp.memory`)
fail-fast on import when the ``mcp`` SDK is missing — that's the
ADR-0004 contract for a Phase 8 install. The Memory-engine wave brings
the dependency in via pyproject.toml; until then we stub the SDK at
import time so the unit tests under ``tests/mcp/`` can exercise the
dispatch + schema layers without the real SDK.

Stubbing rules:

* The stub provides only what our admin / memory modules actually
  reach for: ``mcp.server.fastmcp.FastMCP`` with a no-op ``tool()``
  decorator. Anything else still raises ImportError.
* The stub is installed in ``sys.modules`` BEFORE any test imports
  ``hal0.mcp.admin`` / ``hal0.mcp.memory``. The autouse session-scoped
  fixture below handles that.
* If the real SDK is installed (e.g. once Memory-engine ships pyproject
  changes), we skip the stub and use the real module — the tests are
  expected to keep passing.
"""

from __future__ import annotations

import sys
import types


def _has_real_mcp() -> bool:
    try:
        import mcp.server.fastmcp  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:
        return False


def _install_stub() -> None:
    """Insert a minimal ``mcp.server.fastmcp`` stub into sys.modules.

    Installed at conftest import time — pytest imports conftest BEFORE
    it collects sibling test modules, so the stub is in place by the
    time ``tests/mcp/test_admin.py`` evaluates ``from hal0.mcp import
    admin``. A session-scoped autouse fixture would be too late: it
    runs after collection has already failed.
    """

    class _StubFastMCP:
        def __init__(self, name: str) -> None:
            self.name = name
            self.tools: dict[str, dict[str, object]] = {}

        def tool(self, *, name: str, description: str = ""):
            def _decorator(fn):
                self.tools[name] = {"description": description, "fn": fn}
                return fn

            return _decorator

        def streamable_http_app(self):
            return object()

    fake_mcp = types.ModuleType("mcp")
    fake_server = types.ModuleType("mcp.server")
    fake_fastmcp = types.ModuleType("mcp.server.fastmcp")
    fake_fastmcp.FastMCP = _StubFastMCP  # type: ignore[attr-defined]
    fake_server.fastmcp = fake_fastmcp  # type: ignore[attr-defined]
    fake_mcp.server = fake_server  # type: ignore[attr-defined]
    sys.modules.setdefault("mcp", fake_mcp)
    sys.modules.setdefault("mcp.server", fake_server)
    sys.modules.setdefault("mcp.server.fastmcp", fake_fastmcp)


# Run at import time so collection of test modules that pull in
# ``hal0.mcp.admin`` / ``hal0.mcp.memory`` finds a working SDK.
if not _has_real_mcp():
    _install_stub()
