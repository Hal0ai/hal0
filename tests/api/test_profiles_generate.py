"""Tests for POST /api/profiles/generate — draft profile generation.

Compute-only: never writes to the catalog. HF/LLM calls are mocked via
``httpx.MockTransport`` (same pattern as tests/api/test_hf_routes.py); no
real network is involved.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/api/test_profiles_generate.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import SEED_PROFILES
from hal0.registry.model import Model
from hal0.registry.store import ModelRegistry


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def registry(tmp_hal0_home: str) -> ModelRegistry:
    """Same on-disk store the route's internal ModelRegistry() resolves to
    (both read paths.registry_dir(), which honours HAL0_HOME)."""
    return ModelRegistry()


# ── httpx transport patching (mirrors tests/api/test_hf_routes.py) ─────────


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, target: str, handler) -> None:
    real_cls = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_cls(*args, **kwargs)

    monkeypatch.setattr(target, factory)


def _patch_both(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    _patch_httpx(monkeypatch, "hal0.upstreams.huggingface.httpx.AsyncClient", handler)
    _patch_httpx(monkeypatch, "hal0.profiles.generate.httpx.AsyncClient", handler)


def _hf_handler(
    *,
    meta: dict[str, Any] | None = None,
    tree: list[dict[str, Any]] | None = None,
    gguf: dict[str, Any] | None = None,
    llm_content: str | None = None,
    llm_status: int = 200,
    fail_with: type[Exception] | None = None,
):
    def handler(req: httpx.Request) -> httpx.Response:
        if fail_with is not None:
            raise fail_with("simulated transport failure")
        url = str(req.url)
        if req.url.path.endswith("/v1/chat/completions"):
            if llm_content is None:
                return httpx.Response(llm_status, json={"error": "no utility slot"})
            return httpx.Response(
                llm_status, json={"choices": [{"message": {"content": llm_content}}]}
            )
        if "expand" in url:
            return httpx.Response(200, json={"gguf": gguf} if gguf is not None else {})
        if "/tree/main" in url:
            return httpx.Response(200, json=tree or [])
        return httpx.Response(200, json=meta or {})

    return handler


# ── validation ───────────────────────────────────────────────────────────────


def test_generate_requires_a_source(client: TestClient) -> None:
    r = client.post("/api/profiles/generate", json={})
    assert r.status_code == 422


def test_generate_rejects_both_sources(client: TestClient) -> None:
    r = client.post("/api/profiles/generate", json={"model_id": "a", "hf_repo": "org/b"})
    assert r.status_code == 422


# ── model_id path ────────────────────────────────────────────────────────────


def test_generate_model_id_happy_path(client: TestClient, registry: ModelRegistry) -> None:
    registry.add(
        Model(
            id="qwen3-4b-q4_k_m",
            name="Qwen3 4B",
            path="/models/qwen3-4b.gguf",
            capabilities=["chat"],
            quant="Q4_K_M",
        )
    )

    r = client.post("/api/profiles/generate", json={"model_id": "qwen3-4b-q4_k_m"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"profile", "warnings", "sources"}
    assert data["profile"]["kind"] == "hal0.profile"
    assert data["profile"]["profile"]["cloned_from"] in SEED_PROFILES
    assert "registry:qwen3-4b-q4_k_m" in data["sources"]


def test_generate_model_id_not_found(client: TestClient) -> None:
    r = client.post("/api/profiles/generate", json={"model_id": "does-not-exist"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "model.not_found"


def test_generate_never_writes_to_the_catalog(client: TestClient, registry: ModelRegistry) -> None:
    registry.add(Model(id="m1", path="/models/m1.gguf", capabilities=["chat"]))

    before = {p["name"] for p in client.get("/api/profiles").json()}
    r = client.post("/api/profiles/generate", json={"model_id": "m1", "name": "my-draft-profile"})
    assert r.status_code == 200, r.text
    after = {p["name"] for p in client.get("/api/profiles").json()}
    assert before == after
    assert "my-draft-profile" not in after


# ── hf_repo path ─────────────────────────────────────────────────────────────


def test_generate_hf_repo_happy_path(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"]},
        tree=[{"path": "qwen3-8b-q4_k_m.gguf", "size": 5_000_000_000}],
        gguf={"architecture": "qwen3", "context_length": 40960},
    )
    _patch_both(monkeypatch, handler)

    r = client.post("/api/profiles/generate", json={"hf_repo": "unsloth/Qwen3-8B-GGUF"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["profile"]["quant"] == "Q4_K_M"
    assert "huggingface:unsloth/Qwen3-8B-GGUF" in data["sources"]


def test_generate_hf_repo_bad_coordinate(client: TestClient) -> None:
    r = client.post("/api/profiles/generate", json={"hf_repo": "not-a-slug"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "hf.bad_request"


def test_generate_hf_repo_unreachable_returns_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_both(monkeypatch, _hf_handler(fail_with=httpx.ConnectError))

    r = client.post("/api/profiles/generate", json={"hf_repo": "org/repo"})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "hf.unreachable"


def test_generate_hf_repo_not_found_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def not_found_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    _patch_both(monkeypatch, not_found_handler)

    r = client.post("/api/profiles/generate", json={"hf_repo": "org/repo"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "hf.repo_not_found"


# ── use_llm path ─────────────────────────────────────────────────────────────


def test_generate_use_llm_success(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"]},
        tree=[{"path": "model-q4_k_m.gguf", "size": 1_000_000}],
        gguf={"architecture": "qwen3", "context_length": 8192},
        llm_content="Fast local chat with tool use.",
    )
    _patch_both(monkeypatch, handler)

    r = client.post("/api/profiles/generate", json={"hf_repo": "org/repo", "use_llm": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["profile"]["profile"]["intent"] == "Fast local chat with tool use."
    assert "llm:hal0/utility" in data["sources"]
    assert data["warnings"] == []


def test_generate_use_llm_unavailable_degrades_gracefully(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = _hf_handler(
        meta={"tags": ["text-generation"]},
        tree=[{"path": "model-q4_k_m.gguf", "size": 1_000_000}],
        gguf={"architecture": "qwen3", "context_length": 8192},
        llm_status=404,
    )
    _patch_both(monkeypatch, handler)

    r = client.post("/api/profiles/generate", json={"hf_repo": "org/repo", "use_llm": True})
    assert r.status_code == 200, r.text
    data = r.json()
    assert any("LLM summarization unavailable" in w for w in data["warnings"])
