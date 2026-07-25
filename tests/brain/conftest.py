"""Shared fixtures for the hal0-brain steward chat tests.

The steward routes its gated / admin-catalog tool calls through the SAME
``hal0.mcp.admin`` dispatch core the ``/mcp/admin`` mount uses. That core's
tool → REST mapping (``admin._REST_MAP`` / ``admin._PATH_ARGS``) is LAZY
(spec §4.4): it is empty at import and only populated by
``install_admin_route_map`` inside ``create_app`` (production) or
``set_admin_route_map`` (tests). See :mod:`tests.mcp.conftest`, which installs
it for the ``tests/mcp/`` package.

Without an installed map, ``admin._execute_tool`` short-circuits every unmapped
tool to ``{"status": "error", "error": {"code": "mcp.unmapped_tool"}}``. So a
brain test that actually APPROVES and EXECUTES a gated admin tool (e.g.
``model_delete``) got an ``mcp.unmapped_tool`` envelope instead of the real
REST result, and the assertion on the executed payload failed. That was a
test-ISOLATION gap — the brain suite happened to pass only when a
``tests/mcp/`` test had already populated the module-global map earlier in the
same session — NOT a product defect: ``create_app`` always installs the map on
a live box, and the mapping itself (``model_delete`` →
``DELETE /api/models/{model_id}``) is correct in production.

We install the map from ``admin.TOOL_NAME_ALIASES`` — the STATIC route_id →
tool-name table that is admin's own source of truth — rather than by booting
``create_app`` (which reads the box config and mounts feature-gated routers).
Deriving ``(method, path)`` straight from each ``"METHOD:/path"`` route_id
yields exactly the map ``create_app`` would install for these routes, with no
config dependency, so ``pytest tests/brain`` is self-contained.
"""

from __future__ import annotations

import re

import pytest

_PLACEHOLDER = re.compile(r"{(\w+)}")


@pytest.fixture(scope="session")
def _admin_route_map_source() -> tuple[dict, dict]:
    """Build the admin route map once from the static ``TOOL_NAME_ALIASES``."""
    from hal0.mcp import admin

    route_map: dict[str, tuple[str, str]] = {}
    route_path_args: dict[str, tuple[str, ...]] = {}
    for route_id in admin.TOOL_NAME_ALIASES:
        method, path = route_id.split(":", 1)
        route_map[route_id] = (method, path)
        placeholders = tuple(_PLACEHOLDER.findall(path))
        if placeholders:
            route_path_args[route_id] = placeholders
    return route_map, route_path_args


@pytest.fixture(autouse=True)
def _install_admin_route_map(_admin_route_map_source: tuple[dict, dict]) -> None:
    """Re-install the known-good route map before every brain test.

    Function-scoped so a test that mutates the map starts from the real,
    validated map — a cheap dict copy, no rebuild per test.
    """
    from hal0.mcp import admin

    admin.set_admin_route_map(*_admin_route_map_source)
