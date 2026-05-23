"""Tests for the /api/models surface added in the v3 wireup.

Covers two pieces:
  * ``_derive_ns`` — the locked path-shape rule for blessed vs pulled
    (see issue #220 + the v3 brief). Three cases: blessed recipe path,
    pulled path under the model root, and the empty-path edge.
  * ``POST /api/models/inspect`` — HuggingFace metadata + tree fetch with
    httpx mocked. Validates the variant filter (.gguf only, LFS-size
    preferred), the alias body shape, the 502/404 error envelopes, and
    the 5 minute in-process cache.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api.routes import models as models_route
from hal0.registry.model import Model, _derive_ns

# ── _derive_ns ─────────────────────────────────────────────────────────────


def test_derive_ns_blessed_for_recipe_capability_path() -> None:
    """Path under /var/lib/hal0/models/<recipe>/<capability>/ → blessed."""
    m = Model(
        id="qwen3-coder",
        path="/var/lib/hal0/models/qwen3-coder/chat/qwen3-coder-q4_k_m.gguf",
    )
    assert _derive_ns(m) == "blessed"


def test_derive_ns_pulled_for_id_only_path() -> None:
    """Default pull layout /var/lib/hal0/models/<id>/<file> → pulled."""
    m = Model(
        id="hand-pulled",
        path="/var/lib/hal0/models/hand-pulled/hand-pulled-q4_k_m.gguf",
    )
    assert _derive_ns(m) == "pulled"


def test_derive_ns_empty_path_is_pulled() -> None:
    """Edge case: a Model with an unset/whitespace path must not raise."""
    # pydantic forbids an empty path; the helper still has to tolerate
    # a Model-shaped object whose path was wiped post-construction (the
    # serialisation path runs after registry mutations and we don't
    # want a single bad row to crash the whole listing).
    m = Model(id="ghost", path="/tmp/will-be-cleared")
    object.__setattr__(m, "path", "")
    assert _derive_ns(m) == "pulled"


def test_derive_ns_blessed_root_with_only_id_segment_is_pulled() -> None:
    """Only one path segment after the blessed root → not blessed.

    The rule requires <recipe>/<capability>/<file> — anything shorter
    is the legacy single-segment pull layout.
    """
    m = Model(id="x", path="/var/lib/hal0/models/x/file.gguf")
    assert _derive_ns(m) == "pulled"


def test_derive_ns_arbitrary_root_is_pulled() -> None:
    """A path outside the blessed root is always pulled."""
    m = Model(id="ext", path="/mnt/ai-models/qwen/qwen3-8b/q4.gguf")
    assert _derive_ns(m) == "pulled"


# ── /api/models response shape ─────────────────────────────────────────────


@pytest.fixture
def inspect_app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app with the inspect cache cleared so each test sees a cold cache."""
    models_route._INSPECT_CACHE.clear()
    return create_app()


@pytest.fixture
def inspect_client(inspect_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(inspect_app) as c:
        yield c


def test_list_models_attaches_ns_for_registry_entries(
    inspect_client: TestClient,
    tmp_hal0_home: str,
) -> None:
    """Local registry rows must carry the derived ``ns`` field."""
    fpath = Path(tmp_hal0_home) / "fixture.gguf"
    fpath.write_bytes(b"\x00" * 8)
    # Register two rows: one whose path looks blessed, one whose doesn't.
    inspect_client.post(
        "/api/models",
        json={
            "id": "blessed-row",
            "path": "/var/lib/hal0/models/qwen3-coder/chat/qwen3-coder.gguf",
        },
    )
    inspect_client.post(
        "/api/models",
        json={"id": "pulled-row", "path": str(fpath)},
    )

    body = inspect_client.get("/api/models").json()
    rows = {m["id"]: m for m in body["models"]}
    assert rows["blessed-row"]["ns"] == "blessed"
    assert rows["pulled-row"]["ns"] == "pulled"


def test_get_model_attaches_ns(inspect_client: TestClient, tmp_hal0_home: str) -> None:
    """GET /api/models/{id} carries the same ``ns`` derivation."""
    fpath = Path(tmp_hal0_home) / "x.gguf"
    fpath.write_bytes(b"\x00")
    inspect_client.post("/api/models", json={"id": "g1", "path": str(fpath)})
    row = inspect_client.get("/api/models/g1").json()
    assert row["ns"] == "pulled"


# ── POST /api/models/inspect ───────────────────────────────────────────────


def _hf_handler(
    *,
    meta_status: int = 200,
    tree_status: int = 200,
    meta_body: dict[str, Any] | None = None,
    tree_body: list[dict[str, Any]] | None = None,
    fail_with: type[Exception] | None = None,
):
    """Build an httpx MockTransport handler for the inspect tests."""

    def handler(req: httpx.Request) -> httpx.Response:
        if fail_with is not None:
            raise fail_with("simulated transport failure")
        path = req.url.path
        if path.endswith("/tree/main"):
            return httpx.Response(tree_status, json=tree_body or [])
        # Meta endpoint
        return httpx.Response(meta_status, json=meta_body or {})

    return handler


def _patch_httpx_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Patch ``httpx.AsyncClient`` so the inspect route uses our mock transport.

    The route constructs its own ``AsyncClient`` for the HF fetch. We
    intercept by replacing the class with a thin wrapper that injects
    ``transport=MockTransport(handler)``.
    """
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr("hal0.api.routes.models.httpx.AsyncClient", factory)


def test_inspect_returns_gguf_variants_sorted_by_size(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route surfaces .gguf entries with LFS size + sorts ascending."""
    tree = [
        {"path": "README.md", "size": 4096},
        {
            "path": "qwen3-8b-q4_k_m.gguf",
            "lfs": {"size": 4_900_000_000},
            "size": 132,
        },
        {
            "path": "qwen3-8b-q8_0.gguf",
            "lfs": {"size": 8_500_000_000},
            "size": 132,
        },
        {"path": "tokenizer.json", "size": 1024},
    ]
    meta = {
        "tags": ["text-generation", "gguf"],
        "cardData": {"license": "apache-2.0", "description": "Hello world."},
    }
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body=meta, tree_body=tree),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "unsloth/Qwen3-8B-GGUF"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "unsloth/Qwen3-8B-GGUF"
    ids = [v["id"] for v in body["variants"]]
    assert ids == ["qwen3-8b-q4_k_m.gguf", "qwen3-8b-q8_0.gguf"]
    assert body["variants"][0]["size_bytes"] == 4_900_000_000
    assert "gguf" in body["tags"]
    assert body["metadata"]["license"] == "apache-2.0"
    assert "Hello world" in body["metadata"]["readme_excerpt"]


def test_inspect_accepts_hf_url_alias(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hf_url`` is accepted as an alias for ``hf_repo``."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=[]),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_url": "https://huggingface.co/foo/bar"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "foo/bar"
    assert body["variants"] == []


def test_inspect_caches_response_for_repeated_calls(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 5 minute in-process cache prevents a second HF hit on the
    second click."""
    hits = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        hits["count"] += 1
        if req.url.path.endswith("/tree/main"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"tags": []})

    _patch_httpx_transport(monkeypatch, handler)
    r1 = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/cached"})
    r2 = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/cached"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["cached"] is True
    # Each fresh fetch issues two requests (meta + tree); a cached call
    # issues none.
    assert hits["count"] == 2


def test_inspect_returns_502_when_hf_unreachable(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transport failure surfaces as ``hf.unreachable`` with status 502."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(fail_with=httpx.ConnectError),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "org/down"},
    )
    assert r.status_code == 502, r.text
    body = r.json()
    assert body["error"]["code"] == "hf.unreachable"
    assert body["error"]["details"]["repo"] == "org/down"


def test_inspect_returns_404_when_repo_missing(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 from HF surfaces as ``hf.repo_not_found``."""
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_status=404, meta_body={"error": "not found"}),
    )

    r = inspect_client.post(
        "/api/models/inspect",
        json={"hf_repo": "org/missing"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "hf.repo_not_found"


def test_inspect_rejects_missing_repo_input(inspect_client: TestClient) -> None:
    """Either ``hf_repo`` or ``hf_url`` must be present + non-empty."""
    r = inspect_client.post("/api/models/inspect", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "hf.bad_request"


def test_inspect_rejects_non_org_name_input(inspect_client: TestClient) -> None:
    """Single-token inputs like 'qwen' are rejected as not org/name."""
    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "qwen"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "hf.bad_request"


def test_inspect_bad_json_returns_400(inspect_client: TestClient) -> None:
    """Non-JSON bodies are rejected with the validation envelope."""
    r = inspect_client.post(
        "/api/models/inspect",
        content=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400, r.text


# ── Negative: inspect must not eat HF's pointer-file sizes ─────────────────


def test_inspect_falls_back_to_top_level_size_when_no_lfs(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For non-LFS files we fall back to the top-level ``size``."""
    tree = [
        {"path": "tiny.gguf", "size": 12_345},
    ]
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=tree),
    )

    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/tiny"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["variants"][0]["size_bytes"] == 12_345


# ── Smoke: ensure JSON content-type is what gets returned ──────────────────


def test_inspect_response_is_application_json(
    inspect_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_httpx_transport(
        monkeypatch,
        _hf_handler(meta_body={"tags": []}, tree_body=[]),
    )
    r = inspect_client.post("/api/models/inspect", json={"hf_repo": "org/x"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    # And the body is parseable JSON.
    json.loads(r.content)
