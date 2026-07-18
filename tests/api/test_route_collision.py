"""REWORK.md §J route-collision ratchet.

Starlette/FastAPI matches ``app.routes`` in *registration order* and
dispatches to the first route whose compiled pattern matches -- there is no
"most specific wins" tie-break. That means a literal route (e.g. ``GET
/api/slots/by-id``) registered AFTER a parameterized route whose pattern
also matches that literal path (e.g. ``GET /api/slots/{name}``) is
permanently unreachable: every request meant for the literal handler
silently dispatches to the parameterized one instead. This bug class has
bitten before -- the recent SLOT re-key lane added literal ``/rename``,
``/by-id``, ``/by-name`` routes near ``/api/slots/{name}``.

This test walks the FINAL flattened ``app.routes`` off a real
``create_app()`` instance -- post ``include_router``, post prefixes, the
only table Starlette actually dispatches against -- and for every literal
route checks every ``APIRoute`` registered before it for a pattern
collision.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from hal0.api import create_app

# ---------------------------------------------------------------------------
# Pre-existing debt allowlist.
#
# Empty as of 2026-07-18 -- a full walk of create_app()'s route table found
# zero literal routes shadowed by an earlier parameterized route (the SLOT
# re-key lane's /by-name/{name}, /by-id/{slot_id} and /{name}/rename routes
# register ahead of the bare /api/slots/{name} catch-alls, so none of their
# literal-prefixed paths are shadowed). Kept as an explicit constant rather
# than omitted so a future
# genuine collision has an established place to land -- with a comment
# explaining why it's pre-existing debt -- instead of someone reflexively
# loosening the assertion in test_no_literal_route_shadowed_by_earlier_
# parameterized_route. Entries are (method, literal_path, shadowing_param_path).
#
# test_allowlist_entries_are_still_collisions fails the moment a listed
# entry stops being a real collision, so fixed entries can't linger here.
ALLOWLIST: frozenset[tuple[str, str, str]] = frozenset()


def _api_routes_in_registration_order(app: FastAPI) -> list[APIRoute]:
    """Return only the concrete ``APIRoute`` instances from ``app.routes``,
    in the exact order Starlette's router matches them.

    Mounted sub-apps (``starlette.routing.Mount`` -- the MCP JSON-RPC
    servers) and non-API routes (the docs/openapi ``Route`` entries,
    ``APIWebSocketRoute`` handshakes) are skipped: none of them carry
    FastAPI's ``{param}`` path-template semantics, and this check is
    scoped to APIRoute-vs-APIRoute collisions.
    """
    return [route for route in app.routes if isinstance(route, APIRoute)]


def _non_head_methods(route: APIRoute) -> set[str]:
    """Drop HEAD -- FastAPI auto-adds it to every GET route with identical
    matching semantics, so it must not be treated as an independently
    registered method that needs its own collision check."""
    return {method for method in (route.methods or set()) if method != "HEAD"}


def _is_literal(route: APIRoute) -> bool:
    """True when every segment of the route's path template is a fixed
    string -- i.e. it contains no ``{param}`` placeholder at all."""
    return "{" not in route.path


def _find_collisions(routes: list[APIRoute]) -> list[tuple[str, str, str]]:
    """Return every ``(method, literal_path, shadowing_param_path)`` collision.

    For a literal route L at index ``i``, any parameterized route P at
    index ``j < i`` whose compiled ``path_regex`` -- the exact matcher
    Starlette runs at request time, so this handles ``{param}``,
    ``{param:int}``, ``{param:path}``, and any custom converter without
    reimplementing FastAPI's path-template grammar -- fullmatches L's
    literal path, while sharing at least one HTTP method, permanently
    shadows L (P is registered first, so P wins the match every time).
    """
    collisions: list[tuple[str, str, str]] = []
    for i, literal_route in enumerate(routes):
        if not _is_literal(literal_route):
            continue
        literal_methods = _non_head_methods(literal_route)
        if not literal_methods:
            continue
        for earlier_route in routes[:i]:
            if _is_literal(earlier_route):
                continue  # only a PARAMETERIZED earlier route can shadow
            shared_methods = literal_methods & _non_head_methods(earlier_route)
            if not shared_methods:
                continue
            if earlier_route.path_regex.fullmatch(literal_route.path) is not None:
                for method in sorted(shared_methods):
                    collisions.append((method, literal_route.path, earlier_route.path))
    return collisions


@pytest.fixture(scope="module")
def api_routes() -> list[APIRoute]:
    app = create_app()
    routes = _api_routes_in_registration_order(app)
    assert len(routes) > 100, (
        f"only found {len(routes)} APIRoute instances -- create_app()'s router "
        "wiring may have changed such that app.routes is no longer the final "
        "flattened table this walker expects (true as of FastAPI 0.136.1)"
    )
    return routes


def test_no_literal_route_shadowed_by_earlier_parameterized_route(
    api_routes: list[APIRoute],
) -> None:
    """No literal ``(method, path)`` outside ALLOWLIST may be unreachable."""
    collisions = _find_collisions(api_routes)
    unexpected = sorted(collision for collision in collisions if collision not in ALLOWLIST)
    assert unexpected == [], (
        "literal route(s) are unreachable -- shadowed by an earlier-registered "
        "parameterized route (Starlette matches app.routes in registration "
        "order and dispatches to the first match):\n"
        + "\n".join(
            f"  {method} {literal!r} shadowed by {shadowing!r}"
            for method, literal, shadowing in unexpected
        )
    )


def test_allowlist_entries_are_still_collisions(api_routes: list[APIRoute]) -> None:
    """Every ALLOWLIST entry must remain a genuine, current collision.

    Once a listed ``(method, literal_path, param_path)`` trio is fixed --
    the literal reordered ahead of the parameterized route, or the
    parameterized route's pattern narrowed so it no longer matches -- it
    silently stops colliding and the allowlist rots into stale debt. Fail
    loudly so the stale entry is deleted rather than accumulating.
    """
    collisions = set(_find_collisions(api_routes))
    stale = sorted(entry for entry in ALLOWLIST if entry not in collisions)
    assert stale == [], (
        "ALLOWLIST entries that are no longer real collisions -- remove them:\n"
        + "\n".join(
            f"  {method} {literal!r} / {shadowing!r}" for method, literal, shadowing in stale
        )
    )
