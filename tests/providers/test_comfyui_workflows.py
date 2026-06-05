"""Unit tests for the OpenAI → ComfyUI workflow translator.

The translator is a pure dict transform — these tests don't touch
the network or load any actual ComfyUI runtime. They verify the
contract every workflow template depends on:

  * model_class → template stem resolution.
  * Prompt / size / batch / seed / steps / cfg substitution.
  * Negative prompt patches the canonical CLIP encode node.
  * Random seed is generated when the caller doesn't supply one.
  * Malformed bodies raise typed ``WorkflowTemplateError``.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.providers.comfyui_workflows import (
    MODEL_CLASS_TO_TEMPLATE,
    WorkflowTemplateError,
    build_workflow,
    template_for_model_class,
)

# ─── template lookup ──────────────────────────────────────────────────────────


def test_template_for_known_model_classes() -> None:
    assert template_for_model_class("sdxl-turbo") == "sdxl_turbo_simple"
    assert template_for_model_class("sd-1.5") == "sd15_simple"
    assert template_for_model_class("SDXL") == "sdxl_turbo_simple"  # case-insensitive


def test_template_for_unknown_falls_back_to_sdxl_turbo() -> None:
    assert template_for_model_class("nonsense") == "sdxl_turbo_simple"
    assert template_for_model_class(None) == "sdxl_turbo_simple"
    assert template_for_model_class("") == "sdxl_turbo_simple"


def test_model_class_table_has_named_entries() -> None:
    """Curated catalogue's image entries map to known templates."""
    for cls in ("sdxl-turbo", "sd-1.5", "flux-klein"):
        assert cls in MODEL_CLASS_TO_TEMPLATE
        assert MODEL_CLASS_TO_TEMPLATE[cls]


# ─── build_workflow happy paths ───────────────────────────────────────────────


def _baseline_body() -> dict[str, Any]:
    return {
        "model": "sdxl-turbo",
        "prompt": "a robot painting a self-portrait",
        "size": "1024x1024",
        "n": 1,
    }


def test_build_workflow_substitutes_prompt() -> None:
    graph, meta = build_workflow(
        body=_baseline_body(),
        model_class="sdxl-turbo",
        ckpt_filename="sd_xl_turbo_1.0_fp16.safetensors",
        request_tag="hal0-test-001",
    )
    # Node 6 in our SDXL Turbo template is the positive CLIPTextEncode.
    assert graph["6"]["inputs"]["text"] == "a robot painting a self-portrait"
    assert meta["template"] == "sdxl_turbo_simple"
    assert meta["filename_prefix"] == "hal0-test-001"


def test_build_workflow_substitutes_size_and_batch() -> None:
    body = {**_baseline_body(), "size": "768x768", "n": 2}
    graph, meta = build_workflow(
        body=body,
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    # EmptyLatentImage node holds the dimensions + batch.
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["5"]["inputs"]["height"] == 768
    assert graph["5"]["inputs"]["batch_size"] == 2
    assert meta["width"] == 768 and meta["height"] == 768


def test_build_workflow_clamps_size_to_safe_range() -> None:
    """64x absurd dimensions should be clamped before they crash the slot."""
    body = {**_baseline_body(), "size": "999999x999999"}
    _, meta = build_workflow(
        body=body,
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    assert meta["width"] <= 2048
    assert meta["height"] <= 2048


def test_build_workflow_seed_override() -> None:
    body = {**_baseline_body(), "extra_body": {"seed": 42}}
    graph, meta = build_workflow(
        body=body,
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    assert graph["3"]["inputs"]["seed"] == 42
    assert meta["seed"] == 42


def test_build_workflow_random_seed_when_unspecified() -> None:
    """Two calls without an explicit seed produce different seeds (probabilistically)."""
    seeds = set()
    for _ in range(8):
        _, meta = build_workflow(
            body=_baseline_body(),
            model_class="sdxl-turbo",
            ckpt_filename="x.safetensors",
            request_tag="t1",
        )
        seeds.add(meta["seed"])
    # 8 calls of secrets.randbelow producing dups is astronomically
    # unlikely; if this ever flakes investigate the seed source.
    assert len(seeds) >= 7


def test_build_workflow_steps_and_cfg_overrides() -> None:
    body = {**_baseline_body(), "extra_body": {"steps": 8, "cfg": 2.5}}
    graph, _ = build_workflow(
        body=body,
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    assert graph["3"]["inputs"]["steps"] == 8
    assert graph["3"]["inputs"]["cfg"] == 2.5


def test_build_workflow_patches_ckpt_filename() -> None:
    """The CheckpointLoaderSimple node holds the actual model filename."""
    graph, _ = build_workflow(
        body=_baseline_body(),
        model_class="sdxl-turbo",
        ckpt_filename="my-custom-model.safetensors",
        request_tag="t1",
    )
    assert graph["4"]["inputs"]["ckpt_name"] == "my-custom-model.safetensors"


def test_build_workflow_negative_prompt_patches_node7() -> None:
    body = {**_baseline_body(), "extra_body": {"negative_prompt": "ugly, blurry"}}
    graph, _ = build_workflow(
        body=body,
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    assert graph["7"]["inputs"]["text"] == "ugly, blurry"


def test_build_workflow_strips_meta_block() -> None:
    """The _meta block is template-only; ComfyUI's /prompt would 422 on it."""
    graph, _ = build_workflow(
        body=_baseline_body(),
        model_class="sdxl-turbo",
        ckpt_filename="x.safetensors",
        request_tag="t1",
    )
    assert "_meta" not in graph


def test_build_workflow_sd15_template_for_sd15_class() -> None:
    body = {**_baseline_body(), "model": "sd-1.5-pruned-emaonly"}
    graph, meta = build_workflow(
        body=body,
        model_class="sd-1.5",
        ckpt_filename="v1-5-pruned-emaonly.safetensors",
        request_tag="t1",
    )
    assert meta["template"] == "sd15_simple"
    # SD 1.5 default in the template is 20 steps + Euler.
    assert graph["3"]["inputs"]["steps"] == 20
    assert graph["3"]["inputs"]["sampler_name"] == "euler"


# ─── error paths ──────────────────────────────────────────────────────────────


def test_build_workflow_empty_prompt_raises() -> None:
    body = {**_baseline_body(), "prompt": ""}
    with pytest.raises(WorkflowTemplateError) as exc:
        build_workflow(
            body=body,
            model_class="sdxl-turbo",
            ckpt_filename="x.safetensors",
            request_tag="t1",
        )
    assert "prompt is required" in exc.value.message
