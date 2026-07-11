"""Lock the hal0-memory REST client paths against the real router (P0).

The router only defines GET /api/memory/list and POST /api/memory/delete.
The client previously called GET /api/memory and DELETE /api/memory/{id},
which 404. This test asserts the client now hits routes the router serves.
"""

from __future__ import annotations

import httpx

from hal0.agents.hermes.plugins.memory_hindsight._client import Hal0MemoryClient
from hal0.api.routes.memory import router


def _router_paths() -> set[tuple[str, str]]:
    out = set()
    for route in router.routes:
        for method in getattr(route, "methods", set()):
            out.add((method, route.path))
    return out


def test_list_and_delete_hit_real_routes():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        return httpx.Response(200, json={"deleted": 1})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="http://x") as http:
        client = Hal0MemoryClient(http_client=http)
        client.list_items(limit=10)
        client.delete("some-id")

    # The router is mounted under /api/memory — its own route.path values
    # are relative (e.g. "/list", "/delete"). Reconstruct full paths.
    paths = _router_paths()
    full_paths = {(m, f"/api/memory{p}") for m, p in paths}
    assert ("GET", "/api/memory/list") in full_paths, f"router paths: {paths}"
    assert ("POST", "/api/memory/delete") in full_paths, f"router paths: {paths}"
    # Client must now hit those full paths, not the old wrong ones.
    assert ("GET", "/api/memory/list") in seen, f"seen: {seen}"
    assert ("POST", "/api/memory/delete") in seen, f"seen: {seen}"
