"""Route-sync check: every REST target in ``hal0.mcp.admin._REST_MAP``
must resolve to a route the live FastAPI app actually registers.

``tests/mcp/`` never imported :func:`hal0.api.create_app` before this
file — the drift table in admin.py (~408-422) documents a case where
the tool catalog's declared route didn't match ``hal0.api.routes``,
caught only by hand-auditing. This test builds the real app and checks
the mapping against it directly, so a future drift (like the
``upstream_update`` PATCH route that was mapped correctly here but
unforwardable by ``_call_rest``) is caught for the *route* half of that
class of bug, not just the *verb-support* half.
"""

from __future__ import annotations

import re

from hal0.api import create_app
from hal0.mcp import admin


def _live_routes() -> set[tuple[str, str]]:
    """(HTTP method, path template) pairs the live app registers."""
    app = create_app()
    live: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        live |= {(method, path) for method in methods}
    return live


def test_rest_map_targets_resolve_to_live_routes() -> None:
    live = _live_routes()
    missing = sorted(
        f"{tool}: {method} {template} not registered"
        for tool, (method, template) in admin._REST_MAP.items()
        if (method, template) not in live
    )
    assert not missing, missing


def test_rest_map_path_placeholders_match_path_args() -> None:
    for tool, (_method, template) in admin._REST_MAP.items():
        placeholders = set(re.findall(r"{(\w+)}", template))
        declared = set(admin._PATH_ARGS.get(tool, ()))
        assert placeholders == declared, (
            f"{tool}: template placeholders {sorted(placeholders)} != _PATH_ARGS {sorted(declared)}"
        )
