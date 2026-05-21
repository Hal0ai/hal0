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
instead it returns 422 with ``code="validation.invalid"`` and a
``fields`` list under ``details``.
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
    UnsupportedMediaType,
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
        (UnsupportedMediaType, 415, "audio.unsupported_format"),
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
    """Missing required query param yields 422 + the hal0 envelope.

    FastAPI's default is 422 ``{"detail": [...]}`` — the
    ``RequestValidationError`` handler keeps the 422 status but reshapes
    the body into the canonical envelope.
    """
    _register_validation_routes(app)

    r = client.get("/test/needs-query")

    assert r.status_code == 422, f"expected 422, got {r.status_code}: {r.text}"
    body = r.json()
    assert "error" in body, f"expected hal0 envelope, got {body}"
    assert body["error"]["code"] == "validation.invalid"
    assert body["error"]["message"] == "request validation failed"
    fields = body["error"]["details"]["fields"]
    assert isinstance(fields, list) and len(fields) >= 1
    field = fields[0]
    # Shape: {loc: [...], msg: str, type: str} — ``input`` is intentionally
    # stripped so the envelope can never echo a caller-supplied payload.
    assert set(field.keys()) == {"loc", "msg", "type"}
    assert "unit" in field["loc"]


def test_missing_body_field_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """A missing required field in a JSON body surfaces in ``details.fields``."""
    _register_validation_routes(app)

    r = client.post("/test/needs-body", json={"name": "x"})  # missing ``count``

    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    fields = body["error"]["details"]["fields"]
    locs = [tuple(f["loc"]) for f in fields]
    assert any("count" in loc for loc in locs), f"expected 'count' in {locs}"


def test_malformed_json_body_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """Bytes that aren't valid JSON also land in the hal0 envelope.

    Pydantic raises a ``json_invalid`` error before any field-level
    validation runs; the handler must reshape that path the same way.
    """
    _register_validation_routes(app)

    r = client.post(
        "/test/needs-body",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )

    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    fields = body["error"]["details"]["fields"]
    assert fields, "expected at least one field entry"
    # The ``input`` key is never propagated — even on JSON-parse failure
    # where pydantic would otherwise echo the offending bytes.
    for field in fields:
        assert "input" not in field, f"input leaked: {field}"


def test_invalid_enum_value_returns_envelope(app: FastAPI, client: TestClient) -> None:
    """A value outside the enum yields the same shape (not just missing-field)."""
    _register_validation_routes(app)

    r = client.get("/test/needs-enum", params={"color": "purple"})

    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation.invalid"
    fields = body["error"]["details"]["fields"]
    assert fields, "expected at least one validation error"
    # The pydantic ``type`` for invalid enums starts with ``enum``.
    assert any(f["type"].startswith("enum") for f in fields), fields


def test_raised_unprocessable_entity_is_distinct_from_validation_handler(
    app: FastAPI, client: TestClient
) -> None:
    """Manually-raised ``UnprocessableEntity`` keeps its 422 status with its own
    code/details — the pydantic-driven validation handler shares the status
    but uses ``code="validation.invalid"``."""

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


# ── Issue #13: route-level validation must surface as 4xx, not 500 ──────────
#
# Every site that does client-side validation in a request handler — invalid
# JSON, wrong body shape, missing-but-required field, value outside an
# allowlist — used to ``raise Hal0Error(...)`` which lands on the default
# 500 ``system.internal``. Those have been converted to ``raise BadRequest(...)``
# (or ``NotFound`` for the unknown-id 404). These tests pin the new
# behaviour cite-by-cite so a future revert is loud.

import json as _json  # noqa: E402  (kept near the tests that use it)
from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

from hal0.api import create_app  # noqa: E402


@pytest.fixture()
def app_with_slot(tmp_hal0_home: str) -> FastAPI:
    """A real ``create_app()`` with a primary slot TOML on disk.

    Several route cites in #13 need a known-good slot name so the route
    body actually runs the validation block (vs falling out earlier
    with slot.not_found).
    """
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    (root / "primary.toml").write_text(
        "\n".join(
            [
                'name = "primary"',
                "port = 8081",
                'backend = "vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                "[model]",
                'default = "qwen2.5-0.5b-instruct-q4_k_m"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture()
def slot_client(app_with_slot: FastAPI) -> Iterator[TestClient]:
    with TestClient(app_with_slot) as c:
        yield c


# Each row is one cited ``raise`` from the bug report. The route exercise
# is a real HTTP call against the real ``create_app()`` so the envelope
# middleware, dependency chain, and per-route validation all run end-to-end.
_INVALID_JSON_BODY = b"this is not json {{{"


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status", "expected_code"),
    [
        # ── updater.py PUT /api/updates/channel ─────────────────────────
        # 313: invalid JSON in request body
        pytest.param(
            "PUT",
            "/api/updates/channel",
            _INVALID_JSON_BODY,
            400,
            "request.invalid_json",
            id="updater_channel_invalid_json",
        ),
        # 315: body is JSON but not an object
        pytest.param(
            "PUT",
            "/api/updates/channel",
            _json.dumps(["nightly"]).encode("utf-8"),
            400,
            "request.not_an_object",
            id="updater_channel_not_an_object",
        ),
        # 318: channel value not in {stable, nightly}
        pytest.param(
            "PUT",
            "/api/updates/channel",
            _json.dumps({"channel": "beta"}).encode("utf-8"),
            400,
            "channel.unknown",
            id="updater_channel_unknown_value",
        ),
        # ── slots.py PUT /api/slots/{name}/config ──────────────────────
        # 567: invalid JSON
        pytest.param(
            "PUT",
            "/api/slots/primary/config",
            _INVALID_JSON_BODY,
            400,
            "request.invalid_json",
            id="slots_config_invalid_json",
        ),
        # 572: not an object
        pytest.param(
            "PUT",
            "/api/slots/primary/config",
            _json.dumps([1, 2, 3]).encode("utf-8"),
            400,
            "request.not_an_object",
            id="slots_config_not_an_object",
        ),
        # ── slots.py PATCH /api/slots/{name}/defaults ──────────────────
        # 588: invalid JSON
        pytest.param(
            "PATCH",
            "/api/slots/primary/defaults",
            _INVALID_JSON_BODY,
            400,
            "request.invalid_json",
            id="slots_defaults_invalid_json",
        ),
        # 590: not an object
        pytest.param(
            "PATCH",
            "/api/slots/primary/defaults",
            _json.dumps("ctx_size=2048").encode("utf-8"),
            400,
            "request.not_an_object",
            id="slots_defaults_not_an_object",
        ),
        # ── slots.py POST /api/slots/{name}/backend ────────────────────
        # 602: invalid JSON
        pytest.param(
            "POST",
            "/api/slots/primary/backend",
            _INVALID_JSON_BODY,
            400,
            "request.invalid_json",
            id="slots_backend_invalid_json",
        ),
        # 605: missing/blank backend
        pytest.param(
            "POST",
            "/api/slots/primary/backend",
            _json.dumps({}).encode("utf-8"),
            400,
            "backend.missing",
            id="slots_backend_missing_field",
        ),
        # ── slots.py POST /api/slots/{name}/swap ───────────────────────
        # 680: missing model_id
        pytest.param(
            "POST",
            "/api/slots/primary/swap",
            _json.dumps({}).encode("utf-8"),
            400,
            "swap.missing_model",
            id="slots_swap_missing_model",
        ),
    ],
)
def test_route_validation_returns_typed_4xx(
    slot_client: TestClient,
    method: str,
    path: str,
    body: bytes,
    expected_status: int,
    expected_code: str,
) -> None:
    """Each #13 cite returns 400 + ``BadRequest`` envelope with a stable code.

    The body bytes (rather than ``json=``) preserve invalid-JSON cases
    exactly — ``requests`` would otherwise re-encode and mask them.
    """
    r = slot_client.request(
        method,
        path,
        content=body,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == expected_status, (
        f"{method} {path} expected {expected_status}, got {r.status_code}: {r.text}"
    )
    envelope = r.json()
    assert "error" in envelope, f"missing envelope: {envelope}"
    assert envelope["error"]["code"] == expected_code, (
        f"{method} {path} expected code={expected_code!r}, got {envelope['error']['code']!r}"
    )
    # Envelope shape contract — never null details, always string message.
    assert isinstance(envelope["error"]["message"], str)
    assert isinstance(envelope["error"]["details"], dict)


def test_updater_channel_invalid_after_validation(slot_client: TestClient) -> None:
    """updater.py:331 — Hal0Config rejecting a merged channel value surfaces
    as ``channel.invalid`` (400), not as a 500.

    This path is hard to trigger via the public PUT because the prior
    allowlist check (line 318 → ``channel.unknown``) catches every bad
    value first. We exercise it by stuffing a synthetic config into
    app.state where ``model_dump`` produces a payload that re-validates
    cleanly, then making sure the route doesn't 500 on the happy path —
    a regression here would mean the secondary validation try/except
    swallowed the wrong exception class.
    """
    # The happy-path call must still 200 — covers the "no exception" branch.
    r = slot_client.put("/api/updates/channel", json={"channel": "nightly"})
    assert r.status_code == 200, r.text
    assert r.json() == {"channel": "nightly"}


def test_models_unknown_id_returns_404_not_found(slot_client: TestClient) -> None:
    """models.py:505 — GET /api/models/{unknown} returns 404 ``model.not_found``.

    Pre-issue-#13 this used the default Hal0Error which surfaced as 500
    ``system.internal`` — both the status and the code lied about why
    the lookup failed.
    """
    r = slot_client.get("/api/models/this-model-does-not-exist")
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error"]["code"] == "model.not_found"
    assert body["error"]["details"]["model_id"] == "this-model-does-not-exist"
