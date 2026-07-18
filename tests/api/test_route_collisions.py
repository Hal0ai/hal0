"""Route-shadow guard for the hal0 FastAPI app (P3-routers review §J).

FastAPI/Starlette match routes in *registration order* and return the first
route whose path-regex + method match. That makes literal paths fragile: a
literal like ``GET /api/models/health`` is silently swallowed by an
earlier-registered parameterized catch-all like ``GET /api/models/{model_id}``
(the request becomes a ``model_id="health"`` lookup that 404s). The ordering
that prevents this lives only in ``api/__init__.py`` include-order + a comment;
this test turns that invariant into a failing check.

The detector flattens every ``APIRoute`` into effective (path, methods) pairs
in the order the router actually evaluates them, then flags any *literal* path
that an *earlier* *parameterized* route (with an overlapping HTTP method) would
match first. A positive-control app proves the detector really catches a shadow
(so a future refactor can't make it vacuously pass).
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.routing import APIRoute

from hal0.api import create_app

try:  # newer FastAPI keeps included routers as lazy wrapper objects
    from fastapi.routing import _IncludedRouter  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — older FastAPI flattens onto app.routes
    _IncludedRouter = ()  # type: ignore[assignment]

_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def _flatten(routes: object, prefix: str = "") -> list[tuple[str, frozenset[str]]]:
    """Return effective ``(full_path, methods)`` pairs in match-evaluation order.

    Recurses through ``_IncludedRouter`` wrappers (combining their prefixes)
    the same way the router's own candidate walk does, so the emitted order is
    the order Starlette tries routes in.
    """
    out: list[tuple[str, frozenset[str]]] = []
    for r in routes:  # type: ignore[union-attr]
        if _IncludedRouter and isinstance(r, _IncludedRouter):
            child_prefix = prefix + (r.include_context.prefix or "")
            out.extend(_flatten(r.original_router.routes, child_prefix))
        elif isinstance(r, APIRoute):
            methods = frozenset(m for m in (r.methods or set()) if m in _HTTP_METHODS)
            out.append((prefix + r.path, methods))
    return out


def _path_to_regex(path: str) -> re.Pattern[str]:
    """Compile a route template into an anchored regex.

    ``{param}`` matches a single path segment; ``{param:path}`` matches across
    ``/`` (Starlette's ``:path`` convertor).
    """
    rx = ""
    for part in re.split(r"(\{[^}]+\})", path):
        if part.startswith("{") and part.endswith("}"):
            rx += r".+" if ":path" in part else r"[^/]+"
        else:
            rx += re.escape(part)
    return re.compile("^" + rx + "$")


def _find_shadows(
    effective: list[tuple[str, frozenset[str]]],
) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return ``(shadowing_param_path, shadowed_literal_path, methods)`` tuples.

    A literal route is shadowed when an *earlier*-registered parameterized route
    with an overlapping HTTP method would match its concrete path first.
    """
    shadows: list[tuple[str, str, tuple[str, ...]]] = []
    for i, (literal_path, literal_methods) in enumerate(effective):
        if "{" in literal_path:
            continue  # only literal paths can be shadowed
        for j in range(i):
            param_path, param_methods = effective[j]
            if "{" not in param_path:
                continue  # only a parameterized earlier route can shadow
            overlap = literal_methods & param_methods
            if not overlap:
                continue
            if _path_to_regex(param_path).match(literal_path):
                shadows.append((param_path, literal_path, tuple(sorted(overlap))))
    return shadows


def test_no_literal_route_is_shadowed_by_an_earlier_parameterized_route() -> None:
    """The real app registers every literal path before any catch-all that would eat it."""
    app = create_app()
    effective = _flatten(app.routes)
    assert effective, "flattener found no APIRoutes — router introspection broke"

    shadows = _find_shadows(effective)
    assert not shadows, (
        "route shadow(s) detected (parameterized route registered before literal):\n"
        + "\n".join(
            f"  {lit} [{','.join(methods)}] is shadowed by earlier {param}"
            for param, lit, methods in shadows
        )
    )


def test_models_health_matches_before_model_id_catch_all() -> None:
    """Regression for the api/__init__ ordering leak the spec calls out.

    ``GET /api/models/health`` (hardware.router) MUST be registered before
    ``GET /api/models/{model_id}`` (models.router) or it 404s as a model lookup.
    """
    app = create_app()
    effective = _flatten(app.routes)
    order = {}
    for idx, (path, methods) in enumerate(effective):
        if "GET" in methods:
            order.setdefault(path, idx)

    assert "/api/models/health" in order, "GET /api/models/health is not registered"
    assert "/api/models/{model_id}" in order, "GET /api/models/{model_id} is not registered"
    assert order["/api/models/health"] < order["/api/models/{model_id}"], (
        "GET /api/models/health is registered AFTER the /api/models/{model_id} "
        "catch-all — it will 404 as a model lookup"
    )


def test_detector_catches_a_deliberate_shadow() -> None:
    """Positive control: the detector flags a param-before-literal ordering.

    Without this, a refactor that broke ``_flatten``/``_find_shadows`` would let
    the real-app assertion pass vacuously.
    """
    app = FastAPI()

    @app.get("/api/things/{thing_id}")
    async def _get_thing(thing_id: str) -> dict[str, str]:  # pragma: no cover
        return {"id": thing_id}

    @app.get("/api/things/health")
    async def _things_health() -> dict[str, str]:  # pragma: no cover
        return {"status": "ok"}

    effective = _flatten(app.routes)
    shadows = _find_shadows(effective)
    assert any(
        lit == "/api/things/health" and param == "/api/things/{thing_id}"
        for param, lit, _ in shadows
    ), f"detector failed to flag the deliberate shadow; got {shadows}"
