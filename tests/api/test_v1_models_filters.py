"""Integration tests — per-upstream model filters + enabled flag on /v1/models.

Spec: docs/superpowers/specs/2026-07-06-upstream-model-filters.md. The
filter applies at the aggregation layer only; the dispatch cache keeps the
full catalog (dispatch behavior is deliberately untested here — see spec
"Testing Decisions").
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.registry.model import Model
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


# ── owned_by filter (#1148) — Hermes de-pollution ─────────────────────────
#
# The raw upstream rows carry owned_by == the upstream name ("openrouter"),
# so ?owned_by=hal0 (or the X-hal0-Model-Filter header Hermes sends) drops
# them all, while ?owned_by=openrouter keeps exactly them. Dispatch is
# unaffected — this only curates the discovery surface.


def test_owned_by_query_filters_out_passthroughs(client: TestClient) -> None:
    _seed_remote(client)
    resp = client.get("/v1/models", params={"owned_by": "hal0"})
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["data"]]
    for mid in OPENROUTER_MODELS:
        assert mid not in ids
    # Every surviving row is genuinely hal0-owned.
    assert all(m["owned_by"] == "hal0" for m in resp.json()["data"])


def test_owned_by_query_keeps_matching_owner(client: TestClient) -> None:
    _seed_remote(client)
    resp = client.get("/v1/models", params={"owned_by": "openrouter"})
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["data"]]
    for mid in OPENROUTER_MODELS:
        assert mid in ids


def test_model_filter_header_matches_query_param(client: TestClient) -> None:
    _seed_remote(client)
    resp = client.get("/v1/models", headers={"X-hal0-Model-Filter": "openrouter"})
    assert resp.status_code == 200, resp.text
    ids = [m["id"] for m in resp.json()["data"]]
    for mid in OPENROUTER_MODELS:
        assert mid in ids
    # And the hal0 filter header excludes them.
    resp2 = client.get("/v1/models", headers={"X-hal0-Model-Filter": "hal0"})
    ids2 = [m["id"] for m in resp2.json()["data"]]
    for mid in OPENROUTER_MODELS:
        assert mid not in ids2


# ── §21.5 registry-detail fold-in + show_all modality filter ─────────────
#
# Extends GET /v1/models with labels/checkpoint/recipe/downloaded (sourced
# from the local model registry row when a raw catalog id happens to be
# registered) and a show_all query param that hides non-chat-modality raw
# catalog ids by default.


def test_registry_detail_folds_into_catalog_row(client: TestClient) -> None:
    """A raw catalog id that resolves in the local model registry surfaces
    labels/checkpoint/recipe + downloaded=True; an id the registry doesn't
    know about gets downloaded=False and no extra detail keys."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="anthropic/claude-sonnet-4",
            path="/var/lib/hal0/models/claude-bucket/chat/model.gguf",
            capabilities=["chat", "vision"],
            quant="Q4_K_M",
        )
    )
    resp = client.get("/v1/models")
    assert resp.status_code == 200, resp.text
    by_id = {m["id"]: m for m in resp.json()["data"]}

    registered = by_id["anthropic/claude-sonnet-4"]
    assert registered["downloaded"] is True
    assert registered["labels"] == ["chat", "vision"]
    assert registered["checkpoint"] == "Q4_K_M"
    assert registered["recipe"] == "claude-bucket"

    unregistered = by_id["anthropic/claude-3-haiku"]
    assert unregistered["downloaded"] is False
    assert "labels" not in unregistered
    assert "checkpoint" not in unregistered
    assert "recipe" not in unregistered


def test_show_all_hides_non_chat_modality_by_default(client: TestClient) -> None:
    """A raw upstream id classified as non-chat (image-gen, per its
    registered ``capabilities``) is hidden by default and restored by
    ``?show_all=true``; genuine chat passthrough ids are unaffected."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="nvidia/llama-3.1-nemotron-70b",
            path="/var/lib/hal0/models/image-bucket/image/sdxl.safetensors",
            capabilities=["image"],
        )
    )
    ids_default = _listed_ids(client)
    assert "nvidia/llama-3.1-nemotron-70b" not in ids_default
    for mid in OPENROUTER_MODELS:
        if mid == "nvidia/llama-3.1-nemotron-70b":
            continue
        assert mid in ids_default

    resp = client.get("/v1/models", params={"show_all": "true"})
    assert resp.status_code == 200, resp.text
    ids_show_all = [m["id"] for m in resp.json()["data"]]
    assert "nvidia/llama-3.1-nemotron-70b" in ids_show_all


def test_show_all_query_param_is_case_and_value_tolerant(client: TestClient) -> None:
    """A handful of truthy spellings all enable show_all; any other value
    (including "false"/garbage) keeps the default hiding behavior."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="google/gemini-2.5-pro",
            path="/var/lib/hal0/models/embed-bucket/embed/model.gguf",
            capabilities=["embed"],
        )
    )
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        resp = client.get("/v1/models", params={"show_all": truthy})
        ids = [m["id"] for m in resp.json()["data"]]
        assert "google/gemini-2.5-pro" in ids, truthy

    resp = client.get("/v1/models", params={"show_all": "false"})
    ids = [m["id"] for m in resp.json()["data"]]
    assert "google/gemini-2.5-pro" not in ids
