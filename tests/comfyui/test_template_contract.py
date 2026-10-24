"""The ``_meta.params`` pointer contract of API-renderable workflow templates (ADR-0037).

A template that the image route can render carries a pointer map in ``_meta.params``:
parameter name -> ``node:<id>.inputs.<field>``. The route patches prompts, sizes and seeds
through those pointers, so it never needs to know which model class a graph belongs to.
"""

from __future__ import annotations

import re
from importlib import resources
from typing import Any

import pytest

from hal0.comfyui.catalog import (
    CATALOG,
    OPTIONAL_PARAMS,
    REQUIRED_PARAMS,
    USER_CKPT_TEMPLATES,
    load_template,
)

_POINTER = re.compile(r"^node:(?P<node>\w+)\.inputs\.(?P<field>\w+)$")

#: Every template the generation route can render: the API-renderable catalog models'
#: templates plus the generic user-checkpoint ones.
CONTRACT_TEMPLATES = sorted(
    {m.template for m in CATALOG.values() if m.api_renderable} | set(USER_CKPT_TEMPLATES.values())
)


def _split(pointer: str) -> tuple[str, str]:
    match = _POINTER.match(pointer)
    assert match, f"malformed pointer {pointer!r}"
    return match["node"], match["field"]


def _graph(stem: str) -> dict[str, Any]:
    return {k: v for k, v in load_template(stem).items() if k != "_meta"}


def _params(stem: str) -> dict[str, str]:
    return load_template(stem)["_meta"]["params"]


def _target(stem: str, key: str) -> tuple[dict[str, Any], str]:
    """The graph node and input field a template's ``key`` pointer names."""
    node_id, field = _split(_params(stem)[key])
    return _graph(stem)[node_id], field


def _shipped_templates() -> list[str]:
    package = resources.files("hal0.providers.workflows")
    return sorted(
        p.name.removesuffix(".json") for p in package.iterdir() if p.name.endswith(".json")
    )


def test_contract_vocabulary() -> None:
    assert REQUIRED_PARAMS == (
        "positive_prompt",
        "width",
        "height",
        "seed",
        "steps",
        "cfg",
        "filename_prefix",
    )
    assert OPTIONAL_PARAMS == ("negative_prompt", "batch_size", "ckpt_name")
    assert not set(REQUIRED_PARAMS) & set(OPTIONAL_PARAMS)
    assert dict(USER_CKPT_TEMPLATES) == {"sdxl": "sdxl_simple", "sd15": "sd15_simple"}


def test_contract_covers_the_renderable_models() -> None:
    assert {
        "SDXL-Lightning-8step",
        "sdxl_turbo_simple",
        "sd15_simple",
        "Qwen-Image-2512-BF16-4-Step-LoRA",
        "Qwen-Image-2512-BF16-20-Steps",
        "sdxl_simple",
    } == set(CONTRACT_TEMPLATES)


@pytest.mark.parametrize("stem", CONTRACT_TEMPLATES)
def test_renderable_templates_have_required_params(stem: str) -> None:
    params = _params(stem)
    assert set(REQUIRED_PARAMS) <= set(params)
    # The vocabulary is closed: a typo'd key would be silently ignored by the route.
    assert set(params) <= set(REQUIRED_PARAMS) | set(OPTIONAL_PARAMS)


@pytest.mark.parametrize("stem", CONTRACT_TEMPLATES)
def test_param_pointers_resolve(stem: str) -> None:
    graph = _graph(stem)
    for key, pointer in _params(stem).items():
        node_id, field = _split(pointer)
        assert node_id in graph, f"{stem}: {key} -> missing node {node_id}"
        inputs = graph[node_id]["inputs"]
        assert field in inputs, f"{stem}: {key} -> node {node_id} has no input {field}"
        assert not isinstance(inputs[field], list), f"{stem}: {key} points at a link, not a value"


@pytest.mark.parametrize("stem", CONTRACT_TEMPLATES)
def test_pointers_follow_the_graph_wiring(stem: str) -> None:
    """Prompt, latent and save pointers land on the nodes the sampler actually reads."""
    graph = _graph(stem)
    sampler_id, _ = _split(_params(stem)["seed"])
    sampler = graph[sampler_id]
    assert sampler["class_type"] == "KSampler"
    for key in ("steps", "cfg"):
        assert _split(_params(stem)[key])[0] == sampler_id
    wired = {
        "positive_prompt": sampler["inputs"]["positive"][0],
        "width": sampler["inputs"]["latent_image"][0],
        "height": sampler["inputs"]["latent_image"][0],
        "batch_size": sampler["inputs"]["latent_image"][0],
        "negative_prompt": sampler["inputs"]["negative"][0],
    }
    for key, node_id in wired.items():
        if key in _params(stem):
            assert _split(_params(stem)[key])[0] == node_id, f"{stem}: {key}"
    save, _ = _target(stem, "filename_prefix")
    assert save["class_type"] == "SaveImage"


@pytest.mark.parametrize("stem", CONTRACT_TEMPLATES)
def test_defaults_match_graph(stem: str) -> None:
    defaults = load_template(stem)["_meta"]["defaults"]
    assert {"steps", "cfg"} <= set(defaults)
    sampler, _ = _target(stem, "steps")
    for name, value in defaults.items():
        assert sampler["inputs"][name] == value, f"{stem}: defaults[{name}]"


@pytest.mark.parametrize("stem", USER_CKPT_TEMPLATES.values())
def test_user_ckpt_templates_expose_ckpt_name(stem: str) -> None:
    loader, field = _target(stem, "ckpt_name")
    assert loader["class_type"] == "CheckpointLoaderSimple"
    assert field == "ckpt_name"


def test_sdxl_simple_is_the_generic_sdxl_graph() -> None:
    loader, field = _target("sdxl_simple", "ckpt_name")
    assert loader["inputs"][field] == "user_checkpoint.safetensors"
    defaults = load_template("sdxl_simple")["_meta"]["defaults"]
    # test_defaults_match_graph pins the KSampler inputs to these same values.
    assert {k: defaults[k] for k in ("steps", "cfg", "sampler_name", "scheduler")} == {
        "steps": 25,
        "cfg": 6.5,
        "sampler_name": "dpmpp_2m",
        "scheduler": "karras",
    }


@pytest.mark.parametrize("stem", _shipped_templates())
def test_no_model_class_left(stem: str) -> None:
    assert "model_class" not in load_template(stem).get("_meta", {})
