"""Unit tests for the OpenAI → ComfyUI workflow translator.

The translator is a pure dict transform — these tests don't touch
the network or load any actual ComfyUI runtime. They verify the
contract every workflow template depends on:

  * Prompt / size / batch / seed / steps / cfg substitution through the
    template's ``_meta.params`` pointers.
  * The negative prompt lands wherever the template's pointer names.
  * ``steps`` / ``cfg`` fall back to the template's ``_meta.defaults``.
  * The checkpoint is patched only on templates that carry a ``ckpt_name`` pointer.
  * Random seed is generated when the caller doesn't supply one.
  * Malformed bodies raise typed ``WorkflowTemplateError``.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.comfyui.catalog import get_model
from hal0.providers import comfyui_workflows
from hal0.providers.comfyui_workflows import (
    WorkflowTemplateError,
    WorkflowTemplateNotFound,
    build_workflow,
    effective_size,
)

_TURBO = "sdxl_turbo_simple"


def _template_of(model_id: str) -> str:
    model = get_model(model_id)
    assert model is not None
    return model.template


def test_build_unknown_template_raises_not_found() -> None:
    with pytest.raises(WorkflowTemplateNotFound):
        build_workflow(body=_baseline_body(), template="no-such-template", request_tag="t1")


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
        template=_TURBO,
        request_tag="hal0-test-001",
    )
    # Node 6 in our SDXL Turbo template is the positive CLIPTextEncode.
    assert graph["6"]["inputs"]["text"] == "a robot painting a self-portrait"
    assert graph["9"]["inputs"]["filename_prefix"] == "hal0-test-001"
    assert meta["template"] == _TURBO
    assert meta["filename_prefix"] == "hal0-test-001"
    # The model-class mapping is gone; the debug envelope no longer carries it.
    assert "model_class" not in meta
    assert "ckpt_filename" not in meta


def test_build_workflow_substitutes_size_and_batch() -> None:
    body = {**_baseline_body(), "size": "768x768", "n": 2}
    graph, meta = build_workflow(body=body, template=_TURBO, request_tag="t1")
    # EmptyLatentImage node holds the dimensions + batch.
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["5"]["inputs"]["height"] == 768
    assert graph["5"]["inputs"]["batch_size"] == 2
    assert meta["width"] == 768 and meta["height"] == 768


def test_build_workflow_clamps_size_to_safe_range() -> None:
    """64x absurd dimensions should be clamped before they crash the slot."""
    body = {**_baseline_body(), "size": "999999x999999"}
    _, meta = build_workflow(body=body, template=_TURBO, request_tag="t1")
    assert meta["width"] <= 2048
    assert meta["height"] <= 2048


@pytest.mark.parametrize(
    ("size", "effective"),
    [
        ("768x768", "768x768"),
        ("1001x1003", "1000x1000"),  # floored to a multiple of 8
        ("5000x20", "2048x64"),  # clamped to 64..2048
        ("999999x999999", "2048x2048"),
        ("1024X768", "1024x768"),  # a capital X
        (" 640x480 ", "640x480"),
        ("banana", "1024x1024"),  # unreadable: the default
        ("1024", "1024x1024"),
        ("", "1024x1024"),
        (None, "1024x1024"),
        # Not a string at all: the default, never an error.
        (1024, "1024x1024"),
        (["640x480"], "1024x1024"),
        ({"w": 64}, "1024x1024"),
    ],
)
def test_effective_size_is_the_size_build_workflow_renders(size: Any, effective: str) -> None:
    """The fit preflight judges ``effective_size``, so it must be the translator's own answer."""
    assert effective_size(size) == effective
    _, meta = build_workflow(
        body={**_baseline_body(), "size": size}, template=_TURBO, request_tag="t1"
    )
    assert f"{meta['width']}x{meta['height']}" == effective


def test_build_workflow_seed_override() -> None:
    body = {**_baseline_body(), "extra_body": {"seed": 42}}
    graph, meta = build_workflow(body=body, template=_TURBO, request_tag="t1")
    assert graph["3"]["inputs"]["seed"] == 42
    assert meta["seed"] == 42


def test_build_workflow_random_seed_when_unspecified() -> None:
    """Two calls without an explicit seed produce different seeds (probabilistically)."""
    seeds = set()
    for _ in range(8):
        _, meta = build_workflow(body=_baseline_body(), template=_TURBO, request_tag="t1")
        seeds.add(meta["seed"])
    # 8 calls of secrets.randbelow producing dups is astronomically
    # unlikely; if this ever flakes investigate the seed source.
    assert len(seeds) >= 7


def test_build_workflow_random_seed_is_js_safe() -> None:
    """A drawn seed stays an exact integer in a JavaScript client (#3073)."""
    for _ in range(64):
        _, meta = build_workflow(body=_baseline_body(), template=_TURBO, request_tag="t1")
        assert 0 <= meta["seed"] <= 2**53 - 1


def test_build_workflow_steps_and_cfg_overrides() -> None:
    body = {**_baseline_body(), "extra_body": {"steps": 8, "cfg": 2.5}}
    graph, _ = build_workflow(body=body, template=_TURBO, request_tag="t1")
    assert graph["3"]["inputs"]["steps"] == 8
    assert graph["3"]["inputs"]["cfg"] == 2.5


def test_build_defaults_from_meta() -> None:
    """No steps / cfg in the body: the template's own ``_meta.defaults`` apply."""
    template = _template_of("qwen-image-lightning")
    graph, meta = build_workflow(body=_baseline_body(), template=template, request_tag="t1")
    assert (meta["steps"], meta["cfg"]) == (4, 1.0)
    assert graph["3"]["inputs"]["steps"] == 4
    assert graph["3"]["inputs"]["cfg"] == 1.0

    graph, meta = build_workflow(
        body=_baseline_body(), template=_template_of("qwen-image"), request_tag="t1"
    )
    assert (meta["steps"], meta["cfg"]) == (20, 2.5)
    assert graph["3"]["inputs"]["steps"] == 20


def test_build_cfg_keeps_the_graphs_number_type() -> None:
    """Qwen's graph (and so its ``_meta.defaults``) has an int cfg; it is not widened to a float."""
    template = _template_of("qwen-image-lightning")
    graph, meta = build_workflow(body=_baseline_body(), template=template, request_tag="t1")
    assert type(graph["3"]["inputs"]["cfg"]) is int
    assert type(meta["cfg"]) is int


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(2, 2), (2.5, 2.5), ("3", 3.0), ("not a number", 1), (None, 1)],
)
def test_build_cfg_accepts_int_or_float_from_the_request(requested: Any, expected: float) -> None:
    template = _template_of("qwen-image-lightning")
    body = {**_baseline_body(), "extra_body": {"cfg": requested}}
    graph, meta = build_workflow(body=body, template=template, request_tag="t1")
    assert graph["3"]["inputs"]["cfg"] == expected
    assert type(graph["3"]["inputs"]["cfg"]) is type(expected)
    assert meta["cfg"] == expected


def test_build_malformed_pointer_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    template = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "_meta": {
            "params": {"positive_prompt": "node:1.text"},
            "defaults": {"steps": 4, "cfg": 1.0},
        },
    }
    monkeypatch.setattr(comfyui_workflows, "_load_template", lambda stem: template)
    with pytest.raises(WorkflowTemplateError) as exc:
        build_workflow(body=_baseline_body(), template="synthetic", request_tag="t1")
    assert "positive_prompt" in exc.value.message


def test_build_patches_negative_via_pointer() -> None:
    template = _template_of("sdxl-lightning")
    assert template == "SDXL-Lightning-8step"
    body = {**_baseline_body(), "extra_body": {"negative_prompt": "ugly, blurry"}}
    graph, meta = build_workflow(body=body, template=template, request_tag="t1")
    assert graph["7"]["inputs"]["text"] == "ugly, blurry"
    assert meta["negative_prompt"] == "ugly, blurry"


def test_build_negative_follows_the_pointer_not_node_7(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template whose negative prompt lives elsewhere is patched there, not at node 7."""
    template = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "_meta": {
            "params": {
                "positive_prompt": "node:1.inputs.text",
                "negative_prompt": "node:2.inputs.text",
            },
            "defaults": {"steps": 4, "cfg": 1.0},
        },
    }
    monkeypatch.setattr(comfyui_workflows, "_load_template", lambda stem: template)
    body = {**_baseline_body(), "extra_body": {"negative_prompt": "blurry"}}
    graph, _ = build_workflow(body=body, template="synthetic", request_tag="t1")
    assert graph["2"]["inputs"]["text"] == "blurry"
    assert graph["1"]["inputs"]["text"] == _baseline_body()["prompt"]


def test_build_leaves_the_negative_alone_when_the_body_has_none() -> None:
    graph, meta = build_workflow(body=_baseline_body(), template=_TURBO, request_tag="t1")
    assert graph["7"]["inputs"]["text"] == ""
    assert meta["negative_prompt"] == ""


def test_build_ckpt_override_on_sdxl_simple() -> None:
    graph, _ = build_workflow(
        body=_baseline_body(),
        template="sdxl_simple",
        request_tag="t1",
        ckpt_override="my-custom-model.safetensors",
    )
    assert graph["4"]["inputs"]["ckpt_name"] == "my-custom-model.safetensors"


def test_build_without_override_keeps_the_baked_checkpoint() -> None:
    graph, _ = build_workflow(body=_baseline_body(), template="sdxl_simple", request_tag="t1")
    assert graph["4"]["inputs"]["ckpt_name"] == "user_checkpoint.safetensors"


@pytest.mark.parametrize(
    "template",
    [
        _template_of("qwen-image"),
        _template_of("qwen-image-lightning"),
        _template_of("sdxl-lightning"),
        _TURBO,
    ],
)
def test_build_ckpt_override_rejected_without_pointer(template: str) -> None:
    with pytest.raises(WorkflowTemplateError) as exc:
        build_workflow(
            body=_baseline_body(),
            template=template,
            request_tag="t1",
            ckpt_override="whatever.safetensors",
        )
    assert exc.value.status == 422
    assert "ckpt_name" in exc.value.message


def test_build_workflow_strips_meta_block() -> None:
    """The _meta block is template-only; ComfyUI's /prompt would 422 on it."""
    graph, _ = build_workflow(body=_baseline_body(), template=_TURBO, request_tag="t1")
    assert "_meta" not in graph


def test_build_strips_meta() -> None:
    for model_id in ("sdxl-lightning", "qwen-image", "sd-1.5-pruned-emaonly"):
        graph, _ = build_workflow(
            body=_baseline_body(), template=_template_of(model_id), request_tag="t1"
        )
        assert "_meta" not in graph
        assert graph, model_id


def test_build_workflow_sd15_template() -> None:
    graph, meta = build_workflow(
        body=_baseline_body(), template=_template_of("sd-1.5-pruned-emaonly"), request_tag="t1"
    )
    assert meta["template"] == "sd15_simple"
    # SD 1.5 default in the template is 20 steps + Euler.
    assert graph["3"]["inputs"]["steps"] == 20
    assert graph["3"]["inputs"]["sampler_name"] == "euler"


# ─── error paths ──────────────────────────────────────────────────────────────


def test_build_workflow_empty_prompt_raises() -> None:
    body = {**_baseline_body(), "prompt": ""}
    with pytest.raises(WorkflowTemplateError) as exc:
        build_workflow(body=body, template=_TURBO, request_tag="t1")
    assert "prompt is required" in exc.value.message


def test_build_template_without_default_steps_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A template that omits ``_meta.defaults.steps`` is an authoring error, not a silent 4."""
    template = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}},
        "_meta": {"params": {"positive_prompt": "node:1.inputs.text"}, "defaults": {"cfg": 1.0}},
    }
    monkeypatch.setattr(comfyui_workflows, "_load_template", lambda stem: template)
    with pytest.raises(WorkflowTemplateError) as exc:
        build_workflow(body=_baseline_body(), template="synthetic", request_tag="t1")
    assert "defaults.steps" in exc.value.message
