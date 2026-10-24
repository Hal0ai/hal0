"""Tests for the RAG / image-gen / web-search dynamic env blocks.

Two layers:

  * ``hal0.openwebui.env_writer.dynamic_env_overrides`` — pure rendering,
    already-resolved inputs in, an overrides dict out.
  * ``hal0.openwebui.wiring`` — the live-truth resolver: reads
    ``capabilities.toml`` and the ComfyUI models tree, builds the baked
    ComfyUI workflow, and calls the renderer above.

The matrix the brief asks for: no embed slot / embed slot / comfyui
present / both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hal0.comfyui import paths
from hal0.comfyui.catalog import CATALOG, component_path
from hal0.openwebui.env_writer import _DYNAMIC_ENV_KEYS, dynamic_env_overrides


def _write_capabilities_toml(home: str, body: str) -> None:
    etc = Path(home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "capabilities.toml").write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def _comfyui_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The ComfyUI models tree under ``tmp_path``: the baked workflow needs the model installed."""
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _install(model_id: str) -> None:
    """Put every component file of catalog model ``model_id`` where an install would."""
    root = paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


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


def test_wiring_status_agents_empty_without_registry(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    assert openwebui_wiring_status()["agents"] == []


def test_wiring_status_agents_lists_managed_upstream_models(tmp_hal0_home: str) -> None:
    from types import SimpleNamespace

    from hal0.openwebui.wiring import openwebui_wiring_status

    def _up(name: str, managed_by: str | None, owned: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(name=name, managed_by=managed_by, owned_models=owned)

    class _Reg:
        def list(self) -> list[SimpleNamespace]:
            return [
                _up("hermes-agent", "agent:hermes", ("hermes-agent",)),
                _up("hermes-coder", "agent:hermes", ("stale-owned",)),
                _up("openai", None, ("gpt-x",)),
            ]

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    status = openwebui_wiring_status(
        upstreams=_Reg(), model_cache={"hermes-coder": ["coder", "hermes-agent"]}
    )
    # cache wins over owned_models; operator upstream ignored; sorted + deduped
    assert status["agents"] == ["coder", "hermes-agent"]


def test_wiring_status_agents_survives_broken_registry(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import openwebui_wiring_status

    class _Boom:
        def list(self) -> list[object]:
            raise RuntimeError("boom")

    _write_capabilities_toml(tmp_hal0_home, _NO_SELECTIONS)
    assert openwebui_wiring_status(upstreams=_Boom())["agents"] == []


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
    the SAME catalog model — never a pasted-in literal (e.g. ODS's SDXL
    Lightning graph), and never a checkpoint hal0 doesn't actually ship."""
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides
    from hal0.providers.comfyui_workflows import build_workflow

    _install("sdxl-turbo")
    _write_capabilities_toml(tmp_hal0_home, _IMG_ONLY)
    overrides = resolve_dynamic_env_overrides()

    model = CATALOG["sdxl-turbo"]
    ckpt_file = model.components[0].relpath
    # A fixed seed so this build is byte-comparable to wiring's own call —
    # build_workflow() draws a random seed per call otherwise.
    expected_graph, _meta = build_workflow(
        body={
            "prompt": "a placeholder prompt",
            "size": model.sizes[0],
            "extra_body": {"seed": 0},
        },
        template=model.template,
        request_tag="openwebui",
    )
    got_graph = json.loads(overrides["COMFYUI_WORKFLOW"])
    got_graph["3"]["inputs"]["seed"] = 0
    assert got_graph == expected_graph
    # The checkpoint filename baked into the graph is the one hal0 actually
    # ships for this catalog id — sdxl-turbo, not ODS's Lightning checkpoint.
    assert got_graph["4"]["inputs"]["ckpt_name"] == ckpt_file
    assert ckpt_file != "sdxl_lightning_4step.safetensors"

    nodes = json.loads(overrides["COMFYUI_WORKFLOW_NODES"])
    node_types = {n["type"] for n in nodes}
    assert {"prompt", "width", "height", "steps", "seed", "n"} <= node_types
    prompt_node = next(n for n in nodes if n["type"] == "prompt")
    assert prompt_node["node_ids"] == ["6"]
    # OWUI writes its configured model id (a catalog id, never a checkpoint filename) into
    # a "model" node, so it is never given one: the baked graph already carries the checkpoint.
    assert "model" not in node_types


def test_owui_baked_workflow_uses_catalog_template(tmp_hal0_home: str) -> None:
    """The baked graph is the resolved model's own template, at its native size, and the node
    map comes from that template's pointers."""
    from hal0.comfyui.catalog import load_template, parse_pointer
    from hal0.openwebui.wiring import _owui_baked_workflow

    _install("sd-1.5-pruned-emaonly")

    built = _owui_baked_workflow("sd-1.5-pruned-emaonly")

    assert built is not None
    graph = json.loads(built[0])
    nodes = json.loads(built[1])
    params = load_template("sd15_simple")["_meta"]["params"]
    # The sd15 template is the one rendered, at 512x512 rather than the translator's 1024 default.
    width_node, width_field = parse_pointer(params["width"])
    assert graph[width_node]["inputs"][width_field] == 512
    ckpt_node, ckpt_field = parse_pointer(params["ckpt_name"])
    assert graph[ckpt_node]["inputs"][ckpt_field] == "v1-5-pruned-emaonly.safetensors"
    by_type = {n["type"]: n for n in nodes}
    assert by_type["prompt"] == {
        "type": "prompt",
        "key": "text",
        "node_ids": [parse_pointer(params["positive_prompt"])[0]],
    }
    assert "model" not in by_type


def test_owui_baked_workflow_bakes_a_user_checkpoint_override(tmp_hal0_home: str) -> None:
    """A ``ckpt/<stem>`` selection bakes the operator's file into the generic template."""
    import struct

    from hal0.comfyui.catalog import load_template, parse_pointer
    from hal0.openwebui.wiring import _owui_baked_workflow

    raw = json.dumps(
        {"conditioner.embedders.1.x": {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}
    ).encode("utf-8")
    ckpt = paths.models_root() / "checkpoints" / "sub" / "mine.safetensors"
    ckpt.parent.mkdir(parents=True)
    ckpt.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00")

    built = _owui_baked_workflow("ckpt/mine")

    assert built is not None
    graph = json.loads(built[0])
    node_id, field = parse_pointer(load_template("sdxl_simple")["_meta"]["params"]["ckpt_name"])
    assert graph[node_id]["inputs"][field] == "sub/mine.safetensors"
    # The OWUI "model" node stays out for a user checkpoint too.
    assert "model" not in {n["type"] for n in json.loads(built[1])}


@pytest.mark.parametrize(
    "model_id",
    [
        "sdxl-turbo",  # a catalog model that is not installed
        "wan22-t2v",  # not API-renderable
        "ckpt/absent",  # no such user checkpoint
        "my-custom-checkpoint.safetensors",  # not an id at all
        "",
    ],
)
def test_owui_baked_workflow_is_none_when_the_model_does_not_resolve(
    tmp_hal0_home: str, model_id: str
) -> None:
    from hal0.openwebui.wiring import _owui_baked_workflow

    assert _owui_baked_workflow(model_id) is None


def test_wiring_both_slots_renders_both_blocks(tmp_hal0_home: str) -> None:
    from hal0.openwebui.wiring import resolve_dynamic_env_overrides

    _install("sdxl-turbo")
    _write_capabilities_toml(tmp_hal0_home, _BOTH)
    overrides = resolve_dynamic_env_overrides()
    assert overrides["RAG_EMBEDDING_MODEL"] == "nomic-embed-text-v1.5"
    assert overrides["IMAGE_GENERATION_MODEL"] == "sdxl-turbo"
    assert overrides["ENABLE_IMAGE_GENERATION"] == "True"


def test_wiring_image_model_that_does_not_resolve_is_not_wired(
    tmp_hal0_home: str,
) -> None:
    """A model that resolves to nothing renderable has no workflow to bake,
    so images stay unwired rather than claim a default that cannot render."""
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
    assert overrides["ENABLE_IMAGE_GENERATION"] is None
    assert overrides["COMFYUI_WORKFLOW"] is None
