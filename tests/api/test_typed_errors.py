"""Tests for the typed 4xx ``Hal0Error`` subclasses and the
``RequestValidationError`` envelope handler.

Each subclass test asserts:
- The correct HTTP status is set on the response.
- The response body is the canonical envelope shape
  ``{"error": {"code", "message", "details"}}``.
- A per-instance ``code=`` override propagates through the middleware.
- A ``details=`` dict round-trips.

The pydantic-validation test asserts that a missing query parameter
no longer returns FastAPI's default 422 ``{"detail": [...]}`` shape;
instead it returns 400 with ``code="validation.invalid"`` and an
``errors`` list under ``details``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel

from hal0.api.middleware.error_codes import install as install_error_codes
from hal0.errors import (
    BadRequest,
    Conflict,
    Forbidden,
    Hal0Error,
    NotFound,
    Unauthorized,
    UnprocessableEntity,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def app() -> FastAPI:
    """A minimal FastAPI app with only the error-envelope middleware installed.

    Keeps these tests isolated from the full ``create_app()`` lifespan so they
    exercise the error path and nothing else.
    """
    app = FastAPI()
    install_error_codes(app)
    return app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ── Subclass status + envelope round-trip ───────────────────────────────────


@pytest.mark.parametrize(
    ("exc_cls", "expected_status", "expected_default_code"),
    [
        (BadRequest, 400, "validation.invalid"),
        (Unauthorized, 401, "auth.required"),
        (Forbidden, 403, "auth.forbidden"),
        (NotFound, 404, "resource.not_found"),
        (Conflict, 409, "resource.conflict"),
        (UnprocessableEntity, 422, "validation.unprocessable"),
    ],
)
def test_typed_4xx_subclass_renders_envelope(
    app: FastAPI,
    client: TestClient,
    exc_cls: type[Hal0Error],
    expected_status: int,
    expected_default_code: str,
) -> None:
    """Each subclass surfaces with the right status and default code, and
    the envelope keeps the constructor-supplied message and details."""

    @app.get(f"/test/{exc_cls.__name__.lower()}")
    async def _raise() -> None:
        raise exc_cls(
            f"{exc_cls.__name__} message",
            details={"k": "v"},
        )

    r = client.get(f"/test/{exc_cls.__name__.lower()}")
    assert r.status_code == expected_status

    body = r.json()
    assert "error" in body, f"missing envelope wrapper: {body}"
    err = body["error"]
    assert err["code"] == expected_default_code
    assert err["message"] == f"{exc_cls.__name__} message"
    assert err["details"] == {"k": "v"}


def test_code_override_propagates(app: FastAPI, client: TestClient) -> None:
    """Passing ``code=`` to a subclass constructor overrides the class default."""

    @app.get("/test/code-override")
    async def _raise() -> None:
        raise BadRequest("bad slot name", code="slot.invalid_name")

    r = client.get("/test/code-override")
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "slot.invalid_name"
    assert body["error"]["message"] == "bad slot name"


def test_details_default_empty_dict(app: FastAPI, client: TestClient) -> None:
    """Omitting details yields ``details: {}`` in the envelope (never null)."""

    @app.get("/test/no-details")
    async def _raise() -> None:
        raise NotFound("slot 'primary' not found")

    r = client.get("/test/no-details")
    body = r.json()
    assert body["error"]["details"] == {}


def test_existing_subclass_pattern_still_works(app: FastAPI, client: TestClient) -> None:
    """The constructor change must not break the pre-existing pattern of
    sub-subclassing for stable codes (e.g. ``AuthRequired`` in
    ``hal0.api.middleware.auth``)."""

    class TeapotError(Hal0Error):
        code = "test.teapot"
        status = 418

    @app.get("/test/teapot")
    async def _raise() -> None:
        raise TeapotError("teapot")

    r = client.get("/test/teapot")
    assert r.status_code == 418
    assert r.json()["error"]["code"] == "test.teapot"


# ── FastAPI RequestValidationError handler ──────────────────────────────────


class _Color(StrEnum):
    red = "red"
    blue = "blue"


class _Body(BaseModel):
    name: str
    count: int


def _register_validation_routes(app: FastAPI) -> None:
    @app.get("/test/needs-query")
    async def _needs_query(unit: str = Query(...)) -> dict[str, Any]:
        return {"unit": unit}

    @app.post("/test/needs-body")
    async def _needs_body(body: _Body) -> dict[str, Any]:
        return body.model_dump()

    @app.get("/test/needs-enum")
    async def _needs_enum(color: _Color = Query(...)) -> dict[str, Any]:
        return {"color": color.value}


def test_missing_query_param_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """Missing required query param yields 400 + the hal0 envelope.

    FastAPI's default is 422 ``{"detail": [...]}`` — the
    ``RequestValidationError`` handler downgrades to 400 (caller input
    error) and reshapes into the canonical envelope.
    """
    _register_validation_routes(app)

    r = client.get("/test/needs-query")

    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    body = r.json()
    assert "error" in body, f"expected hal0 envelope, got {body}"
    assert body["error"]["code"] == "validation.invalid"
    assert body["error"]["message"] == "request validation failed"
    errors = body["error"]["details"]["errors"]
    assert isinstance(errors, list) and len(errors) >= 1
    err = errors[0]
    # Shape: {loc: [...], msg: str, type: str}
    assert set(err.keys()) == {"loc", "msg", "type"}
    assert "unit" in err["loc"]


def test_missing_body_field_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """A missing required field in a JSON body surfaces in ``details.errors``."""
    _register_validation_routes(app)

    r = client.post("/test/needs-body", json={"name": "x"})  # missing ``count``

    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    errors = body["error"]["details"]["errors"]
    locs = [tuple(e["loc"]) for e in errors]
    assert any("count" in loc for loc in locs), f"expected 'count' in {locs}"


def test_invalid_enum_value_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """A value outside the enum yields the same shape (not just missing-field)."""
    _register_validation_routes(app)

    r = client.get("/test/needs-enum", params={"color": "purple"})

    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    errors = body["error"]["details"]["errors"]
    assert errors, "expected at least one validation error"
    # The pydantic ``type`` for invalid enums starts with ``enum``.
    assert any(e["type"].startswith("enum") for e in errors), errors


def test_raised_unprocessable_entity_is_distinct_from_validation_handler(
    app: FastAPI, client: TestClient
) -> None:
    """Manually-raised ``UnprocessableEntity`` keeps its 422 status — only the
    pydantic-driven validation path is downgraded to 400."""

    @app.post("/test/manual-422")
    async def _raise() -> None:
        raise UnprocessableEntity(
            "start_at must precede end_at",
            code="schedule.invalid_window",
            details={"start_at": 10, "end_at": 5},
        )

    r = client.post("/test/manual-422")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "schedule.invalid_window"
    assert body["error"]["details"] == {"start_at": 10, "end_at": 5}
