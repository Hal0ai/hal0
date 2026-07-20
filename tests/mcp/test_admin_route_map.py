"""Autogen unit tests for ``build_admin_route_map`` (spec §4.4).

These build a small synthetic FastAPI app and drive the generator directly
(``build_admin_route_map`` is pure — it never installs), so we can assert the
route-id extraction, PATCH support, and the Gap-2 transport/prefix excludes
without needing the whole live route table. The collision + alias-survival
assertions use the real installed map (conftest fixture).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from hal0.mcp import admin


def _fixture_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/things")
    def _list() -> dict:
        return {}

    @app.get("/api/things/{name}")
    def _get(name: str) -> dict:
        return {}

    @app.patch("/api/things/{name}")
    def _patch(name: str) -> dict:
        return {}

    @app.post("/api/things/{name}/child/{cid}")
    def _child(name: str, cid: str) -> dict:
        return {}

    # ── Gap-2: streaming / SSE surfaces (must be skipped) ────────────────
    @app.get("/api/things/{name}/logs/stream")
    def _log_stream(name: str) -> StreamingResponse:
        return StreamingResponse(iter(()))

    @app.get("/api/events")
    def _events() -> dict:
        return {}

    @app.get("/api/things/events")
    def _thing_events() -> dict:
        return {}

    # ── skip prefixes ────────────────────────────────────────────────────
    @app.get("/docs/oauth2-redirect")
    def _docs() -> dict:
        return {}

    # ── SPA catch-all ────────────────────────────────────────────────────
    @app.get("/{full_path:path}")
    def _spa(full_path: str) -> dict:
        return {}

    return app


def test_build_extracts_route_ids_and_path_args() -> None:
    route_map, path_args = admin.build_admin_route_map(_fixture_app())

    assert route_map["GET:/api/things"] == ("GET", "/api/things")
    assert route_map["GET:/api/things/{name}"] == ("GET", "/api/things/{name}")
    # PATCH is a supported verb (Gap-2).
    assert route_map["PATCH:/api/things/{name}"] == ("PATCH", "/api/things/{name}")
    # Placeholders extracted in order.
    assert path_args["GET:/api/things/{name}"] == ("name",)
    assert path_args["POST:/api/things/{name}/child/{cid}"] == ("name", "cid")
    # No path args recorded for a placeholder-free route.
    assert "GET:/api/things" not in path_args


def test_transport_and_prefix_and_spa_excluded() -> None:
    route_map, _ = admin.build_admin_route_map(_fixture_app())
    ids = set(route_map)
    # Streaming / SSE endpoints never become scaffolding.
    assert "GET:/api/things/{name}/logs/stream" not in ids
    assert "GET:/api/events" not in ids
    assert "GET:/api/things/events" not in ids
    # /docs prefix + SPA catch-all skipped.
    assert not any(r.startswith("GET:/docs") for r in ids)
    assert "GET:/{full_path}" not in ids
    # HEAD (Starlette auto-add for GET) is not a supported verb.
    assert not any(r.startswith("HEAD:") for r in ids)


def test_unclassified_route_is_scaffolded_but_not_a_tool() -> None:
    """A route with no TOOL_NAME_ALIASES entry lands in the map but is not
    exposed — deny-by-default (Gap 1)."""
    route_map, _ = admin.build_admin_route_map(_fixture_app())
    rid = "GET:/api/things"
    assert rid in route_map  # scaffolding present
    assert rid not in admin.TOOL_NAME_ALIASES  # but never a tool


def test_collision_two_tool_names_one_route_id() -> None:
    """slot_edit + model_assign both alias PUT:/api/slots/{name}/config."""
    rid = "PUT:/api/slots/{name}/config"
    assert admin.TOOL_NAME_ALIASES[rid] == ("slot_edit", "model_assign")
    # Both reconstruct to the SAME (method, path) via the installed map.
    assert (
        admin._REST_MAP["slot_edit"]
        == admin._REST_MAP["model_assign"]
        == (
            "PUT",
            "/api/slots/{name}/config",
        )
    )


def test_aliased_logs_routes_survive_stream_marker() -> None:
    """logs_tail / slot_logs end in ``/logs`` but are classified — the alias
    protects them from the Gap-2 ``/logs`` suffix marker."""
    assert admin._REST_MAP["logs_tail"] == ("GET", "/api/logs")
    assert admin._REST_MAP["slot_logs"] == ("GET", "/api/slots/{name}/logs")


def test_alias_map_covers_every_old_tool_name() -> None:
    """Every REST-routed tool name keeps a stable alias — no tools/list churn."""
    routed = admin._routed_catalog()
    aliased = {t for names in admin.TOOL_NAME_ALIASES.values() for t in names}
    assert routed == aliased


def test_normalize_strips_path_converters() -> None:
    assert admin._normalize_path("/v1/models/{model_id:path}") == "/v1/models/{model_id}"
    assert admin._normalize_path("/api/x/{name:path}/launch") == "/api/x/{name}/launch"
    assert admin._normalize_path("/api/slots/{name}") == "/api/slots/{name}"
