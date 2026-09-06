"""Tests for the RAG / image-gen / web-search dynamic env blocks.

Two layers:

  * ``hal0.openwebui.env_writer.dynamic_env_overrides`` — pure rendering,
    already-resolved inputs in, an overrides dict out.
  * ``hal0.openwebui.wiring`` — the live-truth resolver: reads
    ``capabilities.toml`` and the curated registry, builds the baked
    ComfyUI workflow, and calls the renderer above.

The matrix the brief asks for: no embed slot / embed slot / comfyui
present / both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.openwebui.env_writer import _DYNAMIC_ENV_KEYS, dynamic_env_overrides


def _write_capabilities_toml(home: str, body: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "capabilities.toml").write_text(body, encoding="utf-8")


# ── dynamic_env_overrides (pure renderer) ───────────────────────────────────


def test_no_gates_nulls_every_dynamic_key() -> None:
    """Nothing bound → every dynamic key is explicitly nulled (not merely
    omitted) so a converge re-render deletes stale keys, never leaves
    them."""
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=None,
    )
    assert set(overrides) == set(_DYNAMIC_ENV_KEYS)
    assert all(v is None for v in overrides.values())


def test_embed_only_renders_rag_block_and_nulls_the_rest() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id="nomic-embed-text-v1.5",
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=None,
    )
    assert overrides["RAG_EMBEDDING_ENGINE"] == "openai"
    assert overrides["RAG_OPENAI_API_BASE_URL"] == "http://host.docker.internal:8080/v1"
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert overrides["ENABLE_WEB_SEARCH"] is None


def test_image_only_renders_comfyui_block_and_nulls_rag() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id="sdxl-turbo",
        image_workflow_json='{"3": {}}',
        image_workflow_nodes_json="[]",
        search_provider=None,
    )
    assert overrides["ENABLE_IMAGE_GENERATION"] == "True"
    assert overrides["IMAGE_GENERATION_ENGINE"] == "comfyui"
    assert overrides["COMFYUI_BASE_URL"] == "http://host.docker.internal:8188"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"
    assert overrides["COMFYUI_WORKFLOW"] == '{"3": {}}'
    assert overrides["COMFYUI_WORKFLOW_NODES"] == "[]"
    assert overrides["RAG_EMBEDDING_ENGINE"] is None


def test_image_model_without_both_workflow_strings_is_not_rendered() -> None:
    """A caller that resolved a model id but failed to build its workflow
    must pass a half-built block through as unrendered, not a partial
    ENABLE_IMAGE_GENERATION=True with no COMFYUI_WORKFLOW behind it."""
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id="sdxl-turbo",
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider=None,
    )
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert overrides["COMFYUI_WORKFLOW"] is None


def test_both_embed_and_image_render_together() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id="nomic-embed-text-v1.5",
        image_model_id="sdxl-turbo",
        image_workflow_json='{"3": {}}',
        image_workflow_nodes_json="[]",
        search_provider=None,
    )
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"


def test_search_provider_renders_web_search_block() -> None:
    overrides = dynamic_env_overrides(
        embed_model_id=None,
        image_model_id=None,
        image_workflow_json=None,
        image_workflow_nodes_json=None,
        search_provider={"engine": "searxng", "query_url": "http://searxng:8080/search?q=<query>"},
    )
    assert overrides["ENABLE_WEB_SEARCH"] == "True"
    assert overrides["WEB_SEARCH_ENGINE"] == "searxng"
    assert overrides["SEARXNG_QUERY_URL"] == "http://searxng:8080/search?q=<query>"


# ── hal0.openwebui.wiring (live-truth resolver) ─────────────────────────────


_NO_SELECTIONS = "schema_version = 2\n"

_EMBED_ONLY = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = true
"""

_IMG_ONLY = """
schema_version = 2
[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "sdxl-turbo"
enabled = true
"""

_BOTH = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = true

[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "sdxl-turbo"
enabled = true
"""

_DISABLED = """
schema_version = 2
[selections.embed.embed]
device = "gpu-vulkan"
provider = "llama-server"
model = "nomic-embed-text-v1.5"
enabled = false
"""


@pytest.mark.parametrize(
    ("toml_body", "expect_documents", "expect_images"),
    [
        (_NO_SELECTIONS, False, False),
        (_EMBED_ONLY, True, False),
        (_IMG_ONLY, False, True),
        (_BOTH, True, True),
        (_DISABLED, False, False),
    ],
    ids=["none", "embed-only", "img-only", "both", "selection-present-but-disabled"],
)
def test_wiring_status_matrix(
    tmp_hal0_home: str, toml_body: str, expect_documents: bool, expect_images: bool
) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    _write_capabilities_toml(tmp_hal0_home, toml_body)
    status = openwebui_wiring_status()
    # Chat/voice are unconditional (env_writer's static defaults).
    assert status["chat"] is True
    assert status["voice"] is True
    assert status["documents"] is expect_documents
    assert status["images"] is expect_images
    assert status["web_search"] is False  # no provider seam wired up yet


def test_wiring_no_embed_slot_produces_no_rag_overrides(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] is None
    assert overrides["ENABLE_IMAGE_GENERATION"] is None


def test_wiring_embed_slot_renders_rag_overrides(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(tmp_hal0_home, _EMBED_ONLY)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["ENABLE_IMAGE_GENERATION"] is None


def test_wiring_comfyui_slot_bakes_the_real_translator_workflow(tmp_hal0_home: str) -> None:
    """The baked COMFYUI_WORKFLOW must match hal0's own translator output for
    the SAME curated model — never a pasted-in literal (e.g. ODS's SDXL
    Lightning graph), and never a checkpoint hal0 doesn't actually ship."""
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides
    from hal0.providers.comfyui_workflows import build_workflow
    from hal0.registry.curated import get_curated

    _write_capabilities_toml(tmp_hal0_home, _IMG_ONLY)
    overrides = resolve_dynamic_env_overrides()

    curated = get_curated("sdxl-turbo")
    assert curated is not None
    # A fixed seed so this build is byte-comparable to wiring's own call —
    # build_workflow() draws a random seed per call otherwise.
    expected_graph, _meta = build_workflow(
        body={"prompt": "a placeholder prompt", "extra_body": {"seed": 0}},
        model_class=curated.model_class,
        ckpt_filename=curated.hf_file,
        request_tag="openwebui",
    )
    got_graph = json.loads(overrides["COMFYUI_WORKFLOW"])
    got_graph["3"]["inputs"]["seed"] = 0
    assert got_graph == expected_graph
    # The checkpoint filename baked into the graph is the one hal0 actually
    # ships for this curated id — sdxl-turbo, not ODS's Lightning checkpoint.
    assert got_graph["4"]["inputs"]["ckpt_name"] == curated.hf_file
    assert curated.hf_file != "sdxl_lightning_4step.safetensors"

    nodes = json.loads(overrides["COMFYUI_WORKFLOW_NODES"])
    node_types = {n["type"] for n in nodes}
    assert {"prompt", "model", "width", "height", "steps", "seed", "n"} <= node_types
    prompt_node = next(n for n in nodes if n["type"] == "prompt")
    assert prompt_node["node_ids"] == ["6"]
    model_node = next(n for n in nodes if n["type"] == "model")
    assert model_node["node_ids"] == ["4"]


def test_wiring_both_slots_renders_both_blocks(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(tmp_hal0_home, _BOTH)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"
    assert overrides["ENABLE_IMAGE_GENERATION"] == "True"


def test_wiring_non_curated_image_model_falls_back_to_raw_id_as_checkpoint(
    tmp_hal0_home: str,
) -> None:
    """An operator-pulled model with no curated registry entry still gets a
    workflow — the raw model id is used as the checkpoint filename and the
    template falls back to the generic sdxl_turbo_simple graph (same
    fallback build_workflow already gives an unknown model_class)."""
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _write_capabilities_toml(
        tmp_hal0_home,
        """
schema_version = 2
[selections.img.img]
device = "gpu-vulkan"
provider = "comfyui"
model = "my-custom-checkpoint.safetensors"
enabled = true
""",
    )
    overrides = resolve_dynamic_env_overrides()
    assert overrides["ENABLE_IMAGE_GENERATION"] == "True"
    graph = json.loads(overrides["COMFYUI_WORKFLOW"])
    assert graph["4"]["inputs"]["ckpt_name"] == "my-custom-checkpoint.safetensors"
