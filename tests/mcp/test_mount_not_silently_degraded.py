"""The MCP admin surface must actually mount — a warning is not enough signal.

``create_app`` wraps ``mount_mcp_servers`` in a blanket ``except Exception`` so
a broken MCP never takes the whole API down. That is the right call for
availability, and it is also how a total outage shipped unnoticed: a Starlette
1.x route-table change meant ``build_admin_route_map`` walked a flat
``app.routes`` that no longer contained any ``include_router``'d endpoint, the
generated map came back EMPTY, ``_apply_route_map`` raised "classified route_id
with no live route" for all ~82 admin tools, and the only trace was one
``hal0.mcp.mount_failed`` line in journald. Every fresh install resolving
fastapi>=0.140 had no admin tools at all.

The existing route-sync tests catch the *specific* cause (they compare the
generated map against the live route table). These assert the OUTCOME, so any
future cause — a dependency bump, a mount-order change, a bad overlay — fails
here regardless of mechanism.

Targeted run:
    python -m pytest tests/mcp/test_mount_not_silently_degraded.py -q
"""

from __future__ import annotations

from hal0.api import create_app
from hal0.mcp import admin


def test_mcp_mount_reports_no_error() -> None:
    """``create_app`` records mount failure on app.state rather than only logging."""
    app = create_app()
    assert getattr(app.state, "mcp_mount_error", None) is None, (
        f"MCP failed to mount: {getattr(app.state, 'mcp_mount_error', None)}"
    )


def test_admin_route_map_is_populated_after_boot() -> None:
    """The autogen map is non-empty — the empty-map failure mode, directly.

    ``_ROUTE_MAP`` is empty at import and filled from the live app. An empty map
    after ``create_app()`` means every admin tool resolved to nothing.
    """
    create_app()
    assert admin._ROUTE_MAP, "admin route map is empty after create_app() — no admin tools"


def test_admin_route_map_covers_the_classified_catalog() -> None:
    """Every hand-authored alias resolves, and the map is the expected order of size.

    A partial walk (e.g. one that flattened only the first level of included
    routers) would leave the map non-empty but missing most tools, which the
    bare non-empty check above would not catch.
    """
    create_app()
    missing = sorted(rid for rid in admin.TOOL_NAME_ALIASES if rid not in admin._ROUTE_MAP)
    assert not missing, f"classified route_ids absent from the generated map: {missing}"
