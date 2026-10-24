"""hal0.comfyui.catalog: typed image-model catalog, templates and install state (ADR-0036)."""

from __future__ import annotations

from collections import defaultdict
from importlib import resources
from pathlib import Path

import pytest

from hal0.comfyui import paths as comfyui_paths
from hal0.comfyui.catalog import (
    CATALOG,
    Component,
    ComponentKind,
    ImageModel,
    get_model,
    install_state,
    load_template,
    models_using,
    recommended_model_id,
    render_template,
    template_weight_refs,
    weight_refs,
)

EXPECTED_IDS = [
    # txt2img
    "sdxl-lightning",
    "sdxl-turbo",
    "sd-1.5-pruned-emaonly",
    "qwen-image-lightning",
    "qwen-image",
    # img2img
    "qwen-image-edit-lightning",
    "qwen-image-edit",
    # txt2video
    "ltx2-t2v",
    "ltx2-t2v-bf16",
    "hunyuan15-t2v",
    "wan22-t2v",
    "wan22-t2v-fp16",
    # img2video
    "ltx2-i2v",
    "ltx2-i2v-bf16",
    "hunyuan15-i2v",
    "wan22-i2v",
    "wan22-i2v-fp16",
    # upscale
    "esrgan-4x",
]


@pytest.fixture
def models_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty ComfyUI data root under tmp_path; returns its ``models`` directory."""
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui"))
    return comfyui_paths.models_root()


def _touch(component: Component) -> None:
    component.dest.parent.mkdir(parents=True, exist_ok=True)
    component.dest.touch()


def _component(model_id: str, kind: ComponentKind) -> Component:
    return next(c for c in CATALOG[model_id].components if c.kind is kind)


def _rendered_names(model_id: str) -> set[str]:
    model = CATALOG[model_id]
    return {Path(value).name for _cls, value in weight_refs(render_template(model))}


# ── the table itself ────────────────────────────────────────────────────────


def test_catalog_ids_match_spec() -> None:
    assert list(CATALOG) == EXPECTED_IDS


def test_catalog_is_grouped_by_task_in_display_order() -> None:
    tasks = [m.task for m in CATALOG.values()]
    order = ["txt2img", "img2img", "txt2video", "img2video", "upscale"]
    assert sorted(set(tasks), key=tasks.index) == order
    assert tasks == sorted(tasks, key=order.index)


def test_catalog_keys_match_model_ids() -> None:
    assert all(key == model.id for key, model in CATALOG.items())


def test_get_model() -> None:
    assert get_model("qwen-image") is CATALOG["qwen-image"]
    assert get_model("no-such-model") is None


def test_api_renderable_set() -> None:
    renderable = {m.id for m in CATALOG.values() if m.api_renderable}
    assert renderable == {
        "sdxl-turbo",
        "sd-1.5-pruned-emaonly",
        "sdxl-lightning",
        "qwen-image-lightning",
        "qwen-image",
    }


def test_sizes_first_is_native() -> None:
    assert CATALOG["sdxl-lightning"].sizes[0] == "1024x1024"
    assert CATALOG["sdxl-turbo"].sizes[0] == "512x512"
    assert CATALOG["qwen-image"].sizes[0] == "1328x1328"


@pytest.mark.parametrize(
    "model", [m for m in CATALOG.values() if m.api_renderable], ids=lambda m: m.id
)
def test_renderable_models_declare_wxh_sizes(model: ImageModel) -> None:
    assert model.sizes
    for size in model.sizes:
        width, _, height = size.partition("x")
        assert width.isdigit()
        assert height.isdigit()


# ── recommendation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pool_gb", "expected"),
    [
        (128, "qwen-image-lightning"),
        (96, "qwen-image-lightning"),
        (95.9, "sdxl-lightning"),
        (16, "sdxl-lightning"),
        (15.9, "sd-1.5-pruned-emaonly"),
        (0, "sd-1.5-pruned-emaonly"),
    ],
)
def test_recommended_by_pool(pool_gb: float, expected: str) -> None:
    assert recommended_model_id(pool_gb) == expected


def test_every_recommendation_is_an_api_renderable_catalog_model() -> None:
    for pool_gb in (0, 16, 96):
        model = get_model(recommended_model_id(pool_gb))
        assert model is not None
        assert model.api_renderable


# ── components follow the template (D3) ─────────────────────────────────────


@pytest.mark.parametrize("model", CATALOG.values(), ids=lambda m: m.id)
def test_components_are_exactly_template_refs(model: ImageModel) -> None:
    declared = {c.relpath for c in model.components}
    referenced = {value for _cls, value in weight_refs(render_template(model))}
    assert declared == referenced


def test_precision_variants_reference_their_files() -> None:
    assert {
        "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    } <= _rendered_names("wan22-t2v")
    assert {
        "wan2.2_t2v_high_noise_14B_fp16.safetensors",
        "wan2.2_t2v_low_noise_14B_fp16.safetensors",
        "umt5_xxl_fp16.safetensors",
    } <= _rendered_names("wan22-t2v-fp16")
    assert "ltx-2-19b-dev-fp8.safetensors" in _rendered_names("ltx2-t2v")
    assert "ltx-2-19b-dev.safetensors" not in _rendered_names("ltx2-t2v")
    assert "ltx-2-19b-dev.safetensors" in _rendered_names("ltx2-t2v-bf16")
    assert "ltx-2-19b-dev-fp8.safetensors" not in _rendered_names("ltx2-t2v-bf16")


@pytest.mark.parametrize(
    ("fast", "full"),
    [
        ("ltx2-t2v", "ltx2-t2v-bf16"),
        ("ltx2-i2v", "ltx2-i2v-bf16"),
        ("wan22-t2v", "wan22-t2v-fp16"),
        ("wan22-i2v", "wan22-i2v-fp16"),
    ],
)
def test_precision_pairs_share_one_template(fast: str, full: str) -> None:
    assert CATALOG[fast].template == CATALOG[full].template
    assert CATALOG[fast].file_substitutions
    assert not CATALOG[full].file_substitutions
    assert CATALOG[fast].approx_gb < CATALOG[full].approx_gb


def test_lightning_loras_match_their_base_model() -> None:
    # The 2512 text-to-image graph must load the 2512 LoRA, not the Edit one.
    assert "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors" in _rendered_names(
        "qwen-image-lightning"
    )
    assert "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors" in _rendered_names(
        "qwen-image-edit-lightning"
    )
    assert not any("Edit" in name for name in _rendered_names("qwen-image-lightning"))


def test_every_shipped_template_belongs_to_a_model() -> None:
    package = resources.files("hal0.providers.workflows")
    shipped = {p.name.removesuffix(".json") for p in package.iterdir() if p.name.endswith(".json")}
    assert shipped == {m.template for m in CATALOG.values()}


def test_workflow_filenames_unique() -> None:
    names = [m.workflow_filename for m in CATALOG.values()]
    assert len(names) == len(set(names))
    assert CATALOG["wan22-t2v"].workflow_filename == "wan22-t2v.json"


def test_shared_dest_has_one_source(models_root: Path) -> None:
    sources: dict[Path, set[str]] = defaultdict(set)
    for model in CATALOG.values():
        for component in model.components:
            sources[component.dest].add(component.source_key)
    assert {dest: s for dest, s in sources.items() if len(s) != 1} == {}


def test_component_sources_wellformed() -> None:
    for model in CATALOG.values():
        for component in model.components:
            assert component.hf_repo and component.hf_path, (model.id, component.relpath)
            assert component.approx_gb > 0, (model.id, component.relpath)
            assert component.source_key == f"hf://{component.hf_repo}/{component.hf_path}"


def test_component_dest_follows_kind_subdir(models_root: Path) -> None:
    vae = Component(ComponentKind.vae, "x.safetensors", 0.1, hf_repo="a/b", hf_path="x.safetensors")
    assert vae.dest == models_root / "vae" / "x.safetensors"
    wan_lora = _component("wan22-t2v", ComponentKind.lora)
    assert wan_lora.dest.parent.parent == models_root / "loras"


def test_component_kind_subdirs_match_the_path_owner() -> None:
    assert {kind.subdir for kind in ComponentKind} <= set(comfyui_paths.SUBDIRS.values())
    assert {kind.value: kind.subdir for kind in ComponentKind} == {
        "checkpoint": "checkpoints",
        "diffusion_model": "diffusion_models",
        "text_encoder": "text_encoders",
        "vae": "vae",
        "lora": "loras",
        "upscaler": "upscale_models",
        "clip_vision": "clip_vision",
        "latent_upscaler": "latent_upscale_models",
    }


# ── model arithmetic and immutability ───────────────────────────────────────


def test_approx_gb_is_the_component_sum() -> None:
    model = CATALOG["sdxl-lightning"]
    assert model.approx_gb == round(sum(c.approx_gb for c in model.components), 1)
    assert model.approx_gb == pytest.approx(7.6)


def test_models_are_frozen_and_hashable() -> None:
    model = CATALOG["wan22-t2v"]
    assert hash(model) == hash(model)
    assert len({model, CATALOG["wan22-t2v-fp16"]}) == 2
    with pytest.raises(AttributeError):
        model.label = "x"  # type: ignore[misc]


def test_file_substitutions_are_read_only() -> None:
    subs = CATALOG["ltx2-t2v"].file_substitutions
    assert subs["ltx-2-19b-dev.safetensors"] == "ltx-2-19b-dev-fp8.safetensors"
    with pytest.raises(TypeError):
        subs["a"] = "b"  # type: ignore[index]


def test_file_substitutions_copy_the_caller_dict() -> None:
    source = {"a.safetensors": "b.safetensors"}
    model = ImageModel(
        id="t",
        label="T",
        task="txt2img",
        components=(),
        template="sd15_simple",
        api_renderable=False,
        sizes=(),
        est_seconds=1,
        license="x",
        file_substitutions=source,
    )
    source["c"] = "d"
    assert dict(model.file_substitutions) == {"a.safetensors": "b.safetensors"}


# ── install state ───────────────────────────────────────────────────────────


def test_install_state_transitions(models_root: Path) -> None:
    model = CATALOG["sdxl-lightning"]
    first, *rest = model.components

    empty = install_state(model)
    assert empty.state == "none"
    assert empty.missing == model.components
    assert empty.missing_gb == model.approx_gb

    _touch(first)
    partial = install_state(model)
    assert partial.state == "partial"
    assert partial.missing == tuple(rest)
    assert partial.missing_gb == round(sum(c.approx_gb for c in rest), 1)

    for component in rest:
        _touch(component)
    complete = install_state(model)
    assert complete.state == "complete"
    assert complete.missing == ()
    assert complete.missing_gb == 0


def test_install_state_ignores_a_directory_at_the_dest(models_root: Path) -> None:
    model = CATALOG["sd-1.5-pruned-emaonly"]
    model.components[0].dest.mkdir(parents=True)
    assert install_state(model).state == "none"


def test_shared_component_counts_for_every_model_using_it(models_root: Path) -> None:
    for component in CATALOG["qwen-image"].components:
        _touch(component)
    assert install_state(CATALOG["qwen-image"]).state == "complete"
    # qwen-image-lightning needs only its LoRA on top of the shared files.
    lightning = install_state(CATALOG["qwen-image-lightning"])
    assert lightning.state == "partial"
    assert [c.kind for c in lightning.missing] == [ComponentKind.lora]


def test_models_using(models_root: Path) -> None:
    unet = _component("qwen-image", ComponentKind.diffusion_model)
    assert models_using(unet) == ["qwen-image-lightning", "qwen-image"]
    users = models_using(_component("qwen-image", ComponentKind.text_encoder))
    assert {"qwen-image", "qwen-image-edit", "hunyuan15-t2v", "hunyuan15-i2v"} <= set(users)
    stray = Component(ComponentKind.vae, "nobody.safetensors", 0.1, hf_repo="a/b", hf_path="n")
    assert models_using(stray) == []


# ── templates ───────────────────────────────────────────────────────────────


def test_load_template_keeps_meta() -> None:
    template = load_template("sdxl_turbo_simple")
    assert template["_meta"]["model_class"] == "sdxl-turbo"
    assert template["4"]["class_type"] == "CheckpointLoaderSimple"


def test_load_template_unknown_stem_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_template("no-such-template")


def test_template_weight_refs_reads_graph_nodes() -> None:
    assert template_weight_refs("sdxl_turbo_simple") == {
        ("CheckpointLoaderSimple", "sd_xl_turbo_1.0_fp16.safetensors")
    }
    assert ("UpscaleModelLoader", "4x-UltraSharp.pth") in template_weight_refs("ESRGAN-4x-Upscale")


def test_weight_refs_scans_inputs_only_and_skips_meta() -> None:
    graph = {
        "_meta": {"defaults": {"ckpt_name": "ignored.safetensors"}},
        "1": {
            "class_type": "Loader",
            "inputs": {
                "a": "m.safetensors",
                "b": "n.pth",
                "c": "o.ckpt",
                "d": "p.pt",
                "e": "q.gguf",
                "f": "r.txt",
                "g": ["1", 0],
                "h": 3,
            },
        },
        "2": {"class_type": "NoInputs"},
    }
    assert weight_refs(graph) == {
        ("Loader", "m.safetensors"),
        ("Loader", "n.pth"),
        ("Loader", "o.ckpt"),
        ("Loader", "p.pt"),
        ("Loader", "q.gguf"),
    }


def test_render_template_substitutes_graph_values_and_keeps_meta() -> None:
    model = ImageModel(
        id="t",
        label="T",
        task="txt2img",
        components=(),
        template="sdxl_turbo_simple",
        api_renderable=False,
        sizes=(),
        est_seconds=1,
        license="x",
        file_substitutions={"sd_xl_turbo_1.0_fp16.safetensors": "swapped.safetensors"},
    )
    rendered = render_template(model)
    assert rendered["4"]["inputs"]["ckpt_name"] == "swapped.safetensors"
    assert rendered["_meta"] == load_template("sdxl_turbo_simple")["_meta"]


def test_render_template_returns_a_fresh_copy() -> None:
    model = CATALOG["sd-1.5-pruned-emaonly"]
    first = render_template(model)
    first["4"]["inputs"]["ckpt_name"] = "mutated.safetensors"
    assert render_template(model)["4"]["inputs"]["ckpt_name"] == "v1-5-pruned-emaonly.safetensors"
