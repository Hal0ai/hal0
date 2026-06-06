"""Tests for ``GET /api/hf/search`` (issue #311).

Proxies HuggingFace's public model search (``https://huggingface.co/api/models?search=...``)
and returns a small typed list to drive the dashboard's stubbed "Search HF"
button. The HF call is mocked via ``httpx.MockTransport`` (same pattern the
``/api/models/inspect`` tests use) so no real network is involved.

Contract under test:

* ``q`` is forwarded as the ``search`` query param.
* ``type`` is forwarded as ``pipeline_tag`` when set.
* Result rows are normalised to the dashboard's flat shape
  (``id``, ``downloads``, ``likes``, ``gated``, ``pipeline_tag``, ``library``,
  ``last_modified``) and hard-capped at 20 rows.
* Empty / missing ``q`` returns ``{"results": []}`` without hitting HF.
* Transport failures (timeouts, 5xx, malformed body) degrade to
  ``{"results": []}`` — never 500 the dashboard.
* ``HF_TOKEN`` is forwarded as a Bearer header when set in the env.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import hf as hf_route

# Cap mirrors the route constant — kept in sync so a future raise on the
# route side fails these tests loudly.
_HF_RESULT_CAP = 20


# ── httpx transport patch (same pattern as test_models_routes.py) ─────────


def _hf_search_handler(
    *,
    body: list[dict[str, Any]] | None = None,
    status: int = 200,
    fail_with: type[Exception] | None = None,
    capture: dict[str, Any] | None = None,
):
    """Build a MockTransport handler that records the request it served.

    ``body`` is the JSON list to return. ``status`` is the HTTP status code
    (``200`` unless set). ``fail_with`` raises instead of returning — used
    to simulate a transport failure path.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["url"] = str(req.url)
            capture["headers"] = dict(req.headers)
        if fail_with is not None:
            raise fail_with("simulated transport failure")
        payload = body if body is not None else []
        return httpx.Response(status, json=payload)

    return handler


def _patch_httpx_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Swap the route's ``httpx.AsyncClient`` for one wired to a MockTransport.

    The route constructs its own ``AsyncClient`` for the upstream call; we
    intercept by replacing the class with a thin wrapper that injects
    ``transport=MockTransport(handler)``.
    """
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr("hal0.api.routes.hf.httpx.AsyncClient", factory)


# ── App + client fixtures (cold search cache between cases) ────────────────


@pytest.fixture
def hf_app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app with the route-level search cache cleared."""
    hf_route._SEARCH_CACHE.clear()
    return create_app()


@pytest.fixture
def hf_client(hf_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(hf_app) as c:
        yield c


# ── happy paths ────────────────────────────────────────────────────────────


def test_hf_search_returns_normalised_results(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mocked HF payload is projected onto the dashboard's row shape."""
    upstream = [
        {
            "id": "unsloth/Qwen3-8B-GGUF",
            "downloads": 12345,
            "likes": 42,
            "gated": False,
            "pipeline_tag": "text-generation",
            "library_name": "gguf",
            "last_modified": "2026-05-12T10:00:00.000Z",
            "tags": ["gguf", "text-generation"],
        },
        {
            "id": "BAAI/bge-large-en-v1.5",
            "downloads": 999_999,
            "likes": 7,
            "gated": "manual",  # HF sometimes returns str for gated
            "pipeline_tag": "feature-extraction",
            "library_name": "sentence-transformers",
            "last_modified": "2025-09-01T00:00:00.000Z",
        },
    ]
    _patch_httpx_transport(monkeypatch, _hf_search_handler(body=upstream))

    r = hf_client.get("/api/hf/search", params={"q": "qwen"})
    assert r.status_code == 200, r.text
    rows = r.json()["results"]
    assert len(rows) == 2
    first = rows[0]
    assert first["id"] == "unsloth/Qwen3-8B-GGUF"
    assert first["downloads"] == 12345
    assert first["likes"] == 42
    assert first["gated"] is False
    assert first["pipeline_tag"] == "text-generation"
    assert first["library"] == "gguf"
    # Second row: HF returns a str for gated sometimes — must surface truthy.
    assert rows[1]["gated"] == "manual"
    assert rows[1]["library"] == "sentence-transformers"


def test_hf_search_forwards_query_param(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route maps ``q`` to HF's ``search`` parameter."""
    capture: dict[str, Any] = {}
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(body=[], capture=capture),
    )

    r = hf_client.get("/api/hf/search", params={"q": "llama 3"})
    assert r.status_code == 200
    # httpx percent-encodes the space as + or %20 depending on form-vs-path;
    # accept either.
    assert ("search=llama+3" in capture["url"]) or ("search=llama%203" in capture["url"])


def test_hf_search_forwards_type_filter(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``type=`` becomes HF's ``pipeline_tag`` filter."""
    capture: dict[str, Any] = {}
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(body=[], capture=capture),
    )

    r = hf_client.get("/api/hf/search", params={"q": "embed", "type": "feature-extraction"})
    assert r.status_code == 200
    assert "pipeline_tag=feature-extraction" in capture["url"]


def test_hf_search_caps_results(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 50-row upstream payload is truncated to the dashboard cap."""
    upstream = [
        {
            "id": f"org/m-{i}",
            "downloads": i,
            "likes": 0,
            "gated": False,
            "pipeline_tag": "text-generation",
        }
        for i in range(50)
    ]
    _patch_httpx_transport(monkeypatch, _hf_search_handler(body=upstream))

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200
    rows = r.json()["results"]
    assert len(rows) == _HF_RESULT_CAP
    assert rows[0]["id"] == "org/m-0"
    assert rows[-1]["id"] == f"org/m-{_HF_RESULT_CAP - 1}"


# ── graceful failure / edge cases ──────────────────────────────────────────


def test_hf_search_empty_query_returns_empty_without_calling_hf(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``q`` → empty list + zero HF calls (cheap UX for empty debounce)."""
    hits = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        hits["count"] += 1
        return httpx.Response(200, json=[])

    _patch_httpx_transport(monkeypatch, handler)

    r = hf_client.get("/api/hf/search", params={"q": ""})
    assert r.status_code == 200
    assert r.json() == {"results": []}
    assert hits["count"] == 0

    # Also a missing param entirely.
    r2 = hf_client.get("/api/hf/search")
    assert r2.status_code == 200
    assert r2.json() == {"results": []}
    assert hits["count"] == 0


def test_hf_search_returns_empty_on_transport_error(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ConnectError from the mock → ``{"results": []}``, status 200."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(fail_with=httpx.ConnectError),
    )

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200, r.text
    assert r.json() == {"results": []}


def test_hf_search_returns_empty_on_upstream_5xx(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 503 from HF degrades to an empty list rather than a 5xx envelope."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(status=503, body={"error": "down"}),
    )

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200
    assert r.json() == {"results": []}


def test_hf_search_skips_non_dict_entries(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HF occasionally emits nulls / lists in the response — only dicts count."""
    upstream: list[Any] = [
        None,
        "junk",
        {"id": "org/ok", "downloads": 1, "likes": 0, "gated": False},
    ]
    _patch_httpx_transport(monkeypatch, _hf_search_handler(body=upstream))

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200
    rows = r.json()["results"]
    assert [row["id"] for row in rows] == ["org/ok"]


# ── auth: HF_TOKEN is forwarded when present ───────────────────────────────


def test_hf_search_forwards_hf_token(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HF_TOKEN`` env is forwarded as a Bearer header to huggingface.co."""
    monkeypatch.setenv("HF_TOKEN", "secret-token-xyz")
    capture: dict[str, Any] = {}
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(body=[], capture=capture),
    )

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200
    headers = {k.lower(): v for k, v in capture["headers"].items()}
    assert headers.get("authorization") == "Bearer secret-token-xyz"


def test_hf_search_no_token_header_when_unset(
    hf_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``HF_TOKEN`` env → no Authorization header on the upstream call."""
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    capture: dict[str, Any] = {}
    _patch_httpx_transport(
        monkeypatch,
        _hf_search_handler(body=[], capture=capture),
    )

    r = hf_client.get("/api/hf/search", params={"q": "x"})
    assert r.status_code == 200
    headers = {k.lower(): v for k, v in capture["headers"].items()}
    assert "authorization" not in headers
