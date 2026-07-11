"""Integration tests — per-upstream model filters + enabled flag on /v1/models.

Spec: docs/superpowers/specs/2026-07-06-upstream-model-filters.md. The
filter applies at the aggregation layer only; the dispatch cache keeps the
full catalog (dispatch behavior is deliberately untested here — see spec
"Testing Decisions").
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.upstreams.filters import ModelFilters
from hal0.upstreams.registry import Upstream

OPENROUTER_MODELS = [
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3-haiku",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-r1:free",
    "nvidia/llama-3.1-nemotron-70b",
]


def _seed_remote(client: TestClient, **upstream_kw: object) -> None:
    reg = client.app.state.upstreams
    for u in list(reg.list()):
        reg.remove(u.name)
    reg.add(
        Upstream(
            name="openrouter",
            kind="remote",
            url="https://openrouter.ai/api/v1",
            auth_value_env="OPENROUTER_API_KEY",
            **upstream_kw,  # type: ignore[arg-type]
        )
    )

    async def fake_fetch(name: str) -> list[str]:
        return list(OPENROUTER_MODELS) if name == "openrouter" else []

    reg.fetch_models = fake_fetch  # type: ignore[method-assign]


def _listed_ids(client: TestClient) -> list[str]:
    response = client.get("/v1/models")
    assert response.status_code == 200, response.text
    return [m["id"] for m in response.json()["data"]]


def test_no_filter_advertises_everything(client: TestClient) -> None:
    _seed_remote(client)
    ids = _listed_ids(client)
    for mid in OPENROUTER_MODELS:
        assert mid in ids


def test_include_globs_curate_the_catalog(client: TestClient) -> None:
    _seed_remote(
        client,
        model_filters=ModelFilters.from_lists(include=["anthropic/*", "google/*"]),
    )
    ids = _listed_ids(client)
    assert "anthropic/claude-sonnet-4" in ids
    assert "google/gemini-2.5-pro" in ids
    assert "deepseek/deepseek-r1:free" not in ids
    assert "nvidia/llama-3.1-nemotron-70b" not in ids


def test_exclude_overrides_include(client: TestClient) -> None:
    _seed_remote(
        client,
        model_filters=ModelFilters.from_lists(
            include=["anthropic/*"], exclude=["anthropic/claude-3-haiku"]
        ),
    )
    ids = _listed_ids(client)
    assert "anthropic/claude-sonnet-4" in ids
    assert "anthropic/claude-3-haiku" not in ids


def test_empty_filters_pass_all(client: TestClient) -> None:
    _seed_remote(client, model_filters=ModelFilters())
    ids = _listed_ids(client)
    for mid in OPENROUTER_MODELS:
        assert mid in ids


def test_disabled_upstream_contributes_nothing(client: TestClient) -> None:
    _seed_remote(client, enabled=False)
    ids = _listed_ids(client)
    for mid in OPENROUTER_MODELS:
        assert mid not in ids
