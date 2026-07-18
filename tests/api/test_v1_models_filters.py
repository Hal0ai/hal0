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
# registered) and a show_all query param that hides NON-TEXT-modality raw
# catalog ids by default. Text models (chat + embed + rerank) stay visible;
# only media-gen/audio (image / tts / asr) are hidden unless show_all=true.


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


def test_default_hides_media_modalities_keeps_text(client: TestClient) -> None:
    """The default (show_all=false) view hides image-gen / TTS / ASR raw
    catalog ids but KEEPS text models — chat AND embed AND rerank — since a
    client enumerates /v1/models to find the embeddings/rerank endpoints."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    # Re-tag three of the seeded passthrough ids across the modality
    # spectrum via their registered capabilities.
    registry.add(
        Model(
            id="deepseek/deepseek-r1:free",
            path="/var/lib/hal0/models/embed-bucket/embed/model.gguf",
            capabilities=["embed"],
        )
    )
    registry.add(
        Model(
            id="google/gemini-2.5-pro",
            path="/var/lib/hal0/models/rerank-bucket/rerank/model.gguf",
            capabilities=["rerank"],
        )
    )
    registry.add(
        Model(
            id="nvidia/llama-3.1-nemotron-70b",
            path="/var/lib/hal0/models/image-bucket/image/sdxl.safetensors",
            capabilities=["image"],
        )
    )
    ids_default = _listed_ids(client)
    # Text models — chat passthroughs + the embed + the rerank id — visible.
    assert "anthropic/claude-sonnet-4" in ids_default
    assert "anthropic/claude-3-haiku" in ids_default
    assert "deepseek/deepseek-r1:free" in ids_default  # embed
    assert "google/gemini-2.5-pro" in ids_default  # rerank
    # Media-gen modality hidden by default.
    assert "nvidia/llama-3.1-nemotron-70b" not in ids_default


def test_show_all_reveals_hidden_media_modalities(client: TestClient) -> None:
    """``?show_all=true`` restores the media-gen/audio rows the default
    view hides — image, tts, and asr modalities all reappear."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="anthropic/claude-sonnet-4",
            path="/var/lib/hal0/models/image-bucket/image/sdxl.safetensors",
            capabilities=["image"],
        )
    )
    registry.add(
        Model(
            id="anthropic/claude-3-haiku",
            path="/var/lib/hal0/models/tts-bucket/tts/kokoro.gguf",
            capabilities=["tts"],
        )
    )
    registry.add(
        Model(
            id="google/gemini-2.5-pro",
            path="/var/lib/hal0/models/asr-bucket/asr/whisper.gguf",
            capabilities=["asr"],
        )
    )
    ids_default = _listed_ids(client)
    for hidden in (
        "anthropic/claude-sonnet-4",
        "anthropic/claude-3-haiku",
        "google/gemini-2.5-pro",
    ):
        assert hidden not in ids_default

    resp = client.get("/v1/models", params={"show_all": "true"})
    assert resp.status_code == 200, resp.text
    ids_show_all = [m["id"] for m in resp.json()["data"]]
    for mid in OPENROUTER_MODELS:
        assert mid in ids_show_all


def test_show_all_query_param_is_case_and_value_tolerant(client: TestClient) -> None:
    """A handful of truthy spellings all enable show_all; any other value
    (including "false"/garbage) keeps the default hiding behavior. Uses an
    image (non-text) id, which the default view hides."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="google/gemini-2.5-pro",
            path="/var/lib/hal0/models/image-bucket/image/sdxl.safetensors",
            capabilities=["image"],
        )
    )
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        resp = client.get("/v1/models", params={"show_all": truthy})
        ids = [m["id"] for m in resp.json()["data"]]
        assert "google/gemini-2.5-pro" in ids, truthy

    resp = client.get("/v1/models", params={"show_all": "false"})
    ids = [m["id"] for m in resp.json()["data"]]
    assert "google/gemini-2.5-pro" not in ids


def test_get_model_by_id_bypasses_default_modality_filter(client: TestClient) -> None:
    """GET /v1/models/{id} is an explicit fetch — a valid non-text id
    (hidden from the default LIST) still resolves by id. Its registry
    detail (labels/checkpoint/recipe/downloaded) rides along."""
    _seed_remote(client)
    registry = client.app.state.model_registry
    registry.add(
        Model(
            id="nvidia/llama-3.1-nemotron-70b",
            path="/var/lib/hal0/models/image-bucket/image/sdxl.safetensors",
            capabilities=["image"],
            quant="FP16",
        )
    )
    # Hidden from the default list …
    assert "nvidia/llama-3.1-nemotron-70b" not in _listed_ids(client)
    # … but a direct by-id fetch resolves it (path-encoded slashes).
    resp = client.get("/v1/models/nvidia/llama-3.1-nemotron-70b")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == "nvidia/llama-3.1-nemotron-70b"
    assert body["downloaded"] is True
    assert body["labels"] == ["image"]
    assert body["checkpoint"] == "FP16"
    assert body["recipe"] == "image-bucket"


def test_get_model_by_id_still_honors_owned_by(client: TestClient) -> None:
    """The by-id bypass is modality-only — the ``owned_by`` curation still
    scopes a mismatched-owner id out (unchanged behavior)."""
    _seed_remote(client)
    # openrouter-owned id + owned_by=hal0 → out of scope → 404.
    resp = client.get("/v1/models/anthropic/claude-sonnet-4", params={"owned_by": "hal0"})
    assert resp.status_code == 404, resp.text
    # Same id with the matching owner resolves.
    resp2 = client.get("/v1/models/anthropic/claude-sonnet-4", params={"owned_by": "openrouter"})
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["id"] == "anthropic/claude-sonnet-4"
