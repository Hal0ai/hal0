"""The route-map walker must survive FastAPI's include_router restructuring.

Found live on halo 2026-07-27. FastAPI 0.138 stopped flattening
``include_router``: ``app.routes`` now holds one ``fastapi.routing.
_IncludedRouter`` per included router instead of that router's ``APIRoute``
objects. The wrapper carries neither ``.path`` nor ``.methods``, so
``build_admin_route_map``'s flat one-level walk skipped every one of them and
returned an empty map. ``install_admin_route_map`` then raised "catalog drift:
classified route_id with no live route" for ~85 routes, ``create_app`` swallowed
it as a warning, and **``/mcp/admin`` and ``/mcp/memory`` never mounted** — with
the API still reporting healthy. 21 boots on halo, 0 successful mounts.

CI could not see it: the test venv resolves ``fastapi`` from ``uv.lock``
(0.136.1, still flat) while the installer resolves from ``pyproject.toml``,
which pinned ``fastapi>=0.115`` with no upper bound — so every fresh install
got the restructured version and lost its whole MCP surface.

The walker now descends through any wrapper that can enumerate its children via
``effective_candidates()`` (the accessor 0.138 provides, returning
``_EffectiveRouteContext`` objects whose ``.path`` is already fully resolved,
prefix included). These tests pin that behavior with duck-typed fakes so they
assert the contract on BOTH fastapi generations, plus a version-agnostic
end-to-end check that the real app yields a populated map.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI

from hal0.mcp import admin


class _FakeLeaf:
    """Stands in for ``_EffectiveRouteContext``: resolved path, no prefix math."""

    def __init__(self, path: str, methods: set[str], response_class: Any = None) -> None:
        self.path = path
        self.methods = methods
        self.response_class = response_class


class _FakeIncludedRouter:
    """Stands in for ``fastapi.routing._IncludedRouter``.

    The defining trait is what broke the walker: no ``.path``, no ``.methods``
    — only a callable that expands to the real routes.
    """

    def __init__(self, children: list[Any]) -> None:
        self._children = children

    def effective_candidates(self) -> list[Any]:
        return self._children


class _OpaqueRoute:
    """A wrapper with no way to enumerate children — must be skipped, not raise."""


def test_walker_descends_through_included_router_wrappers() -> None:
    """The regression: wrapped routes must land in the map."""
    routes = [
        _FakeIncludedRouter(
            [
                _FakeLeaf("/api/slots", {"GET"}),
                _FakeLeaf("/api/slots/{name}", {"GET", "DELETE"}),
            ]
        )
    ]
    route_map, path_args = admin.build_admin_route_map(routes)

    assert route_map["GET:/api/slots"] == ("GET", "/api/slots")
    assert route_map["GET:/api/slots/{name}"] == ("GET", "/api/slots/{name}")
    assert route_map["DELETE:/api/slots/{name}"] == ("DELETE", "/api/slots/{name}")
    assert path_args["GET:/api/slots/{name}"] == ("name",)


def test_walker_handles_nested_wrappers() -> None:
    """A router included into a router nests the wrappers — recurse, don't stop."""
    routes = [
        _FakeIncludedRouter([_FakeIncludedRouter([_FakeLeaf("/api/status", {"GET"})])]),
    ]
    route_map, _ = admin.build_admin_route_map(routes)
    assert "GET:/api/status" in route_map


def test_walker_still_reads_flat_routes() -> None:
    """Back-compat: the pre-0.138 flat shape must keep working unchanged."""
    routes = [_FakeLeaf("/api/ports", {"GET"})]
    route_map, _ = admin.build_admin_route_map(routes)
    assert "GET:/api/ports" in route_map


def test_walker_mixes_flat_and_wrapped() -> None:
    routes = [
        _FakeLeaf("/api/ports", {"GET"}),
        _FakeIncludedRouter([_FakeLeaf("/api/status", {"GET"})]),
    ]
    route_map, _ = admin.build_admin_route_map(routes)
    assert {"GET:/api/ports", "GET:/api/status"} <= set(route_map)


def test_walker_skips_unexpandable_wrappers_without_raising() -> None:
    """An unknown wrapper shape degrades to "not a tool", never to a crash."""
    routes = [_OpaqueRoute(), _FakeLeaf("/api/ports", {"GET"})]
    route_map, _ = admin.build_admin_route_map(routes)
    assert set(route_map) == {"GET:/api/ports"}


def test_walker_survives_an_expander_that_raises() -> None:
    class _Exploding:
        def effective_candidates(self) -> list[Any]:
            raise RuntimeError("private API moved again")

    routes = [_Exploding(), _FakeLeaf("/api/ports", {"GET"})]
    route_map, _ = admin.build_admin_route_map(routes)
    assert set(route_map) == {"GET:/api/ports"}


def test_walker_terminates_on_a_self_referential_wrapper() -> None:
    """Depth-bounded: a cycle must not hang the boot path."""

    class _Cycle:
        def effective_candidates(self) -> list[Any]:
            return [self]

    route_map, _ = admin.build_admin_route_map([_Cycle()])
    assert route_map == {}


def test_transport_excludes_still_apply_to_wrapped_routes() -> None:
    """Gap-2 excludes must not be bypassed just because a route was wrapped."""

    class _Stream:
        __name__ = "StreamingResponse"

    routes = [
        _FakeIncludedRouter(
            [
                _FakeLeaf("/api/things/{name}/logs/stream", {"GET"}),
                _FakeLeaf("/api/things/feed", {"GET"}, response_class=_Stream),
                _FakeLeaf("/api/things", {"GET"}),
            ]
        )
    ]
    route_map, _ = admin.build_admin_route_map(routes)
    assert set(route_map) == {"GET:/api/things"}


def test_skip_prefixes_still_apply_to_wrapped_routes() -> None:
    routes = [
        _FakeIncludedRouter([_FakeLeaf("/mcp/admin/x", {"GET"}), _FakeLeaf("/api/things", {"GET"})])
    ]
    route_map, _ = admin.build_admin_route_map(routes)
    assert set(route_map) == {"GET:/api/things"}


def test_real_included_router_is_walked_on_this_fastapi() -> None:
    """Version-agnostic end-to-end: a genuinely included router must be found
    whichever way the installed FastAPI stores it."""
    router = APIRouter()

    @router.get("/api/widgets")
    def _list() -> dict:
        return {}

    @router.post("/api/widgets/{widget_id}")
    def _make(widget_id: str) -> dict:
        return {}

    app = FastAPI()
    app.include_router(router)

    route_map, path_args = admin.build_admin_route_map(app)

    assert "GET:/api/widgets" in route_map
    assert "POST:/api/widgets/{widget_id}" in route_map
    assert path_args["POST:/api/widgets/{widget_id}"] == ("widget_id",)


def test_real_included_router_with_prefix_resolves_the_full_path() -> None:
    """The prefix lives on the wrapper, not the sub-route, in 0.138 — the
    walker must report the path a client would actually call."""
    router = APIRouter()

    @router.get("/things")
    def _list() -> dict:
        return {}

    app = FastAPI()
    app.include_router(router, prefix="/api/nested")

    route_map, _ = admin.build_admin_route_map(app)
    assert "GET:/api/nested/things" in route_map


def test_live_app_route_map_is_populated() -> None:
    """The blocker in one assertion: a real hal0 app must produce a non-empty
    map containing known classified routes. Empty here means no MCP surface."""
    from hal0.api import create_app

    route_map, _ = admin.build_admin_route_map(create_app())

    assert len(route_map) > 50, f"walker found only {len(route_map)} routes"
    for route_id in ("GET:/api/slots", "GET:/api/status", "POST:/api/slots/{name}/restart"):
        assert route_id in route_map, f"{route_id} missing — MCP tools would not mount"
