"""Autogen unit tests for ``build_admin_route_map`` (spec §4.4).

These build a small synthetic FastAPI app and drive the generator directly
(``build_admin_route_map`` is pure — it never installs), so we can assert the
route-id extraction, PATCH support, and the Gap-2 transport/prefix excludes
without needing the whole live route table. The collision + alias-survival
assertions use the real installed map (conftest fixture).
"""

from __future__ import annotations

import pytest
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


# ── ``:path`` converters: the one thing that lets a path argument hold '/' ───


def _converter_app(*, guard_first: bool = False) -> FastAPI:
    app = FastAPI()

    def _plain_launch() -> None:
        @app.post("/api/launch/{name}/go")
        def _launch(name: str) -> dict:
            return {}

    def _guard_launch() -> None:
        @app.post("/api/launch/{name:path}/go")
        def _guard(name: str) -> dict:
            return {}

    @app.delete("/api/files/{root}/{rel:path}")
    def _delete(root: str, rel: str) -> dict:
        return {}

    @app.get("/api/numbered/{n:int}")
    def _numbered(n: int) -> dict:
        return {}

    if guard_first:
        _guard_launch()
        _plain_launch()
    else:
        _plain_launch()
        _guard_launch()
    return app


def test_build_keeps_the_path_converter_on_the_template_but_not_in_the_route_id() -> None:
    route_map, path_args = admin.build_admin_route_map(_converter_app())

    # The route_id stays the bare form TOOL_NAME_ALIASES is written in; the template says
    # which argument may hold '/'. The path-arg NAMES never carry the converter.
    assert route_map["DELETE:/api/files/{root}/{rel}"] == (
        "DELETE",
        "/api/files/{root}/{rel:path}",
    )
    assert path_args["DELETE:/api/files/{root}/{rel}"] == ("root", "rel")
    # Only ``:path`` is kept: ``:int`` and the like still read as a plain segment.
    assert route_map["GET:/api/numbered/{n}"] == ("GET", "/api/numbered/{n}")


@pytest.mark.parametrize("guard_first", [False, True], ids=["plain-first", "guard-first"])
def test_a_route_id_shared_with_a_plain_route_never_gets_the_path_converter(
    guard_first: bool,
) -> None:
    """Two live routes can share one normalized id (a guard route beside the real one).

    The argument may hold '/' only if EVERY route of that id declares it ``:path``, whatever
    order they were registered in: the real route is the one the call must be safe against.
    """
    route_map, path_args = admin.build_admin_route_map(_converter_app(guard_first=guard_first))

    assert route_map["POST:/api/launch/{name}/go"] == ("POST", "/api/launch/{name}/go")
    assert path_args["POST:/api/launch/{name}/go"] == ("name",)


def test_the_installed_map_reads_the_path_converter_back_out_of_the_live_route() -> None:
    rid = "DELETE:/api/comfyui/files/{subdir}/{relpath}"
    # The scaffolding keeps what the live route declares; the tool-level view stays bare.
    assert admin._ROUTE_MAP[rid] == ("DELETE", "/api/comfyui/files/{subdir}/{relpath:path}")
    assert admin._REST_MAP["comfyui_file_delete"] == (
        "DELETE",
        "/api/comfyui/files/{subdir}/{relpath}",
    )
    assert admin._PATH_ARGS["comfyui_file_delete"] == ("subdir", "relpath")
    assert admin._slash_path_args("comfyui_file_delete") == {"relpath"}
    assert admin._slash_path_args("comfyui_workflow_launch") == frozenset()
    assert admin._slash_path_args("not_a_tool") == frozenset()


def test_reinstalling_a_prebuilt_map_keeps_the_path_converter() -> None:
    """``set_admin_route_map(route_map, path_args)`` (the spec §4.3 form) carries it too."""
    route_map, path_args = dict(admin._ROUTE_MAP), dict(admin._ROUTE_PATH_ARGS)
    admin._SLASH_ARGS.clear()

    admin.set_admin_route_map(route_map, path_args)

    assert admin._slash_path_args("comfyui_file_delete") == {"relpath"}


def test_only_a_path_converter_argument_accepts_a_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(admin._SLASH_ARGS, "tool_x", frozenset({"b"}))

    assert admin._path_arg_refusal("tool_x", "b", "p/q.bin") is None
    assert admin._path_arg_refusal("tool_x", "a", "p/q.bin") is not None
    # Even a ``:path`` argument is checked segment by segment.
    for bad in ("p//q", "/p", "p/", "p/../q", "p/./q", "", "..", "p/\x00/q", 1.5, None):
        assert admin._path_arg_refusal("tool_x", "b", bad) is not None, bad


def test_format_url_encodes_each_segment_of_a_path_converter_argument() -> None:
    template = "/api/x/{a}/{b}"

    url = admin._format_url(
        "http://t/",
        template,
        {"a": "q/r?s", "b": "p q/é/100%/x#y"},
        slash_args=frozenset({"b"}),
    )

    assert url == "http://t/api/x/q%2Fr%3Fs/p%20q/%C3%A9/100%25/x%23y"
