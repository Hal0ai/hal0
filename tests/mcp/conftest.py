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


import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _mcp_session_hal0_home(tmp_path_factory: pytest.TempPathFactory):
    """Session-scoped filesystem isolation for the session-scoped app build.

    ``tests/conftest.py``'s ``tmp_hal0_home`` gives every other suite an empty
    config tree, but it is FUNCTION-scoped and ``_admin_route_map_source``
    below is SESSION-scoped — so the app it builds fell through to the host's
    real ``/etc/hal0``. On a box with hal0 installed that file is 0600/0660
    hal0-owned, so all 184 tests under tests/mcp/ ERRORed at fixture setup with
    ``PermissionError: /etc/hal0/hal0.toml`` for any developer who is not root
    and not in the hal0 group. CI runs as root, so CI was green and the suite
    was simply unrunnable locally.

    Same two env vars ``tmp_hal0_home`` sets, applied once per session.
    """
    home = tmp_path_factory.mktemp("hal0-mcp-home")
    mp = pytest.MonkeyPatch()
    mp.setenv("HAL0_HOME", str(home))
    mp.setenv("HAL0_OVERRIDE_DIR", "hal0_home")
    try:
        yield str(home)
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def _admin_route_map_source(_mcp_session_hal0_home: str) -> tuple[dict, dict]:
    """Build the admin route map once from the live app (route-id keyed).

    ``_REST_MAP`` / ``_PATH_ARGS`` are lazy now (spec §4.4): they are empty at
    import and populated by ``install_admin_route_map`` in create_app or
    ``set_admin_route_map`` in a test. Unit tests under ``tests/mcp/`` that
    poke the catalog directly (dispatch, ``_validate_catalog``, ``_REST_MAP``
    assertions) need a map installed — build it once here.
    """
    from hal0.api import create_app
    from hal0.mcp import admin

    create_app()  # installs the map as a side effect of mount_mcp_servers
    return dict(admin._ROUTE_MAP), dict(admin._ROUTE_PATH_ARGS)


@pytest.fixture(autouse=True)
def _install_admin_route_map(_admin_route_map_source: tuple[dict, dict]) -> None:
    """Re-install the known-good route map before every ``tests/mcp/`` test.

    Function-scoped so a test that mutates the map (monkeypatch, or a fake app
    via ``set_admin_route_map``) starts from the real, validated map — cheap
    dict copy, no create_app per test.
    """
    from hal0.mcp import admin

    admin.set_admin_route_map(*_admin_route_map_source)
