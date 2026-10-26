"""hal0.comfyui.resolve: the one place a ``model`` string becomes a template (ADR-0037, spec D9).

Resolution reads the real models tree (``HAL0_COMFYUI_DATA_ROOT`` points it at ``tmp_path``),
so "installed" here means the files really exist.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from hal0.comfyui import paths
from hal0.comfyui.catalog import (
    CATALOG,
    USER_CKPT_TEMPLATES,
    component_path,
    load_template,
    weights_gib,
)
from hal0.comfyui.resolve import (
    ImageModelNotInstalled,
    ImageModelNotRenderable,
    ImageModelRequired,
    ImageModelUnknown,
    ImageWontFit,
    ResolvedImageModel,
    renderable_ids,
    resolve_image_model,
)
from hal0.errors import Hal0Error

_SDXL_KEY = "conditioner.embedders.1.x"
_SD15_KEY = "cond_stage_model.transformer.x"


@pytest.fixture(autouse=True)
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "comfyui-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    assert paths.models_root().is_relative_to(tmp_path)
    return root


def _install(model_id: str) -> None:
    """Put every component file of catalog model ``model_id`` where the install would."""
    root = paths.models_root()
    for component in CATALOG[model_id].components:
        path = component_path(component, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _checkpoint(relpath: str, key: str | None) -> None:
    """A user checkpoint under ``checkpoints/``; ``key=None`` writes bytes that are no safetensors."""
    path = paths.models_root() / "checkpoints" / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    if key is None:
        path.write_bytes(b"not a safetensors file")
        return
    raw = json.dumps({key: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw + b"\x00\x00")


def test_resolve_catalog_complete() -> None:
    _install("sdxl-turbo")

    resolved = resolve_image_model("sdxl-turbo", selection_model=None)

    assert isinstance(resolved, ResolvedImageModel)
    assert resolved.id == "sdxl-turbo"
    assert resolved.template == "sdxl_turbo_simple"
    assert resolved.ckpt_override is None
    # The template's own steps/cfg, plus the model's native size (the first of its sizes).
    defaults = load_template("sdxl_turbo_simple")["_meta"]["defaults"]
    assert resolved.defaults == {**defaults, "size": CATALOG["sdxl-turbo"].sizes[0]}
    assert resolved.defaults["size"] == "512x512"


def test_resolve_catalog_native_size_follows_the_model() -> None:
    _install("sdxl-lightning")
    assert resolve_image_model("sdxl-lightning", selection_model=None).defaults["size"] == (
        "1024x1024"
    )


def test_resolve_defaults_are_a_copy_of_the_template_meta() -> None:
    """Mutating a resolved ``defaults`` must not reach the template the next call loads."""
    _install("sdxl-turbo")
    first = resolve_image_model("sdxl-turbo", selection_model=None)
    first.defaults["steps"] = 999
    assert resolve_image_model("sdxl-turbo", selection_model=None).defaults["steps"] != 999


def test_resolve_not_installed_409() -> None:
    with pytest.raises(ImageModelNotInstalled) as exc:
        resolve_image_model("sdxl-turbo", selection_model=None)

    err = exc.value
    assert isinstance(err, Hal0Error)
    assert (err.code, err.status) == ("image.model_not_installed", 409)
    assert err.details["model"] == "sdxl-turbo"
    assert err.details["install"] == "POST /api/comfyui/models/sdxl-turbo/install"
    assert err.details["missing_gb"] == weights_gib(CATALOG["sdxl-turbo"])  # GiB, not decimal GB
    assert err.details["missing_gb"] == 6.4  # 6.9e9 bytes
    assert "sdxl-turbo" in err.message
    assert "6.4 GB still to download" in err.message


def test_resolve_partial_install_is_not_installed() -> None:
    """A model with some components on disk is still not renderable; ``missing_gb`` is the rest."""
    model = CATALOG["sdxl-lightning"]
    first = model.components[0]
    path = component_path(first, paths.models_root())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")

    with pytest.raises(ImageModelNotInstalled) as exc:
        resolve_image_model("sdxl-lightning", selection_model=None)

    # the LoRA and the VAE: 0.4 + 0.3 decimal GB is 0.7e9 bytes, 0.7 GiB (to one decimal)
    assert exc.value.details["missing_gb"] == round(
        sum(c.approx_gb for c in model.components[1:]) * 1e9 / 2**30, 1
    )


@pytest.mark.parametrize("model_id", ["wan22-t2v", "qwen-image-edit", "esrgan-4x", "ltx2-i2v"])
def test_resolve_video_422(model_id: str) -> None:
    """Video, edit and upscale models are not API-renderable, installed or not."""
    with pytest.raises(ImageModelNotRenderable) as exc:
        resolve_image_model(model_id, selection_model=None)

    err = exc.value
    assert (err.code, err.status) == ("image.model_not_renderable", 422)
    hint = err.details["hint"]
    assert err.details == {"model": model_id, "task": CATALOG[model_id].task, "hint": hint}
    # Says what exists today: the editor on :8188, which needs a ROCm lane, and where it answers.
    assert "ComfyUI editor" in hint and "8188" in hint and "ROCm lane" in hint
    # Where the editor answers depends on [network].comfyui_listen (ADR-0041), so the hint names
    # the port and the dashboard link that knows, and not a bind it cannot see.
    assert "Engine tab" in hint
    assert "loopback" not in hint and "SSH tunnel" not in hint and "LAN" not in hint
    assert "Image Gen" not in hint

    assert hint in err.message


def test_resolve_non_renderable_wins_over_not_installed() -> None:
    """Nothing is installed here, yet the answer is 'cannot be rendered', not 'install it'."""
    with pytest.raises(ImageModelNotRenderable):
        resolve_image_model("wan22-t2v", selection_model=None)


@pytest.mark.parametrize(
    ("key", "arch", "size"),
    [(_SDXL_KEY, "sdxl", "1024x1024"), (_SD15_KEY, "sd15", "512x512")],
)
def test_resolve_user_ckpt(key: str, arch: str, size: str) -> None:
    _checkpoint("my_finetune.safetensors", key)

    resolved = resolve_image_model("ckpt/my_finetune", selection_model=None)

    assert resolved.id == "ckpt/my_finetune"
    assert resolved.template == USER_CKPT_TEMPLATES[arch]
    assert resolved.ckpt_override == "my_finetune.safetensors"
    template_defaults = load_template(USER_CKPT_TEMPLATES[arch])["_meta"]["defaults"]
    assert resolved.defaults == {**template_defaults, "size": size}


def test_resolve_user_ckpt_carries_its_file_size() -> None:
    """The scan's ``size_bytes`` rides along, so the fit preflight needs no second stat."""
    _checkpoint("mine.safetensors", _SDXL_KEY)
    on_disk = (paths.models_root() / "checkpoints" / "mine.safetensors").stat().st_size

    resolved = resolve_image_model("ckpt/mine", selection_model=None)

    assert resolved.size_bytes == on_disk > 0


def test_resolve_catalog_model_has_no_file_size() -> None:
    _install("sdxl-turbo")

    assert resolve_image_model("sdxl-turbo", selection_model=None).size_bytes is None


def test_image_wont_fit_is_a_422_with_a_stable_code() -> None:
    error = ImageWontFit("needs 6.4 GB of GPU memory, 4.0 GB free for a render", {"model": "m"})

    assert (error.status, error.code) == (422, "image.wont_fit")
    assert error.message == "needs 6.4 GB of GPU memory, 4.0 GB free for a render"
    assert error.details == {"model": "m"}


def test_resolve_nested_user_ckpt() -> None:
    """A nested checkpoint is named by its path under ``checkpoints/`` and loads by that path.

    ``a.safetensors`` and ``sub/a.safetensors`` are different files with different ids, so
    naming one can never render the other.
    """
    _checkpoint("a.safetensors", _SDXL_KEY)
    _checkpoint("sub/a.safetensors", _SD15_KEY)
    _checkpoint("anime/v2/waifu.safetensors", _SDXL_KEY)

    top = resolve_image_model("ckpt/a", selection_model=None)
    nested = resolve_image_model("ckpt/sub/a", selection_model=None)
    deep = resolve_image_model("ckpt/anime/v2/waifu", selection_model=None)

    assert (top.id, top.ckpt_override, top.template) == ("ckpt/a", "a.safetensors", "sdxl_simple")
    assert (nested.id, nested.ckpt_override, nested.template) == (
        "ckpt/sub/a",
        "sub/a.safetensors",
        "sd15_simple",
    )
    assert (deep.id, deep.ckpt_override) == ("ckpt/anime/v2/waifu", "anime/v2/waifu.safetensors")


@pytest.mark.parametrize(
    "crafted",
    [
        "ckpt/../present",
        "ckpt/present.safetensors",
        "ckpt/",
        "ckpt//present",
        "ckpt/sub/../present",
        "ckpt/sub/absent",
        "ckpt/waifu",  # the stem alone no longer names a nested file
        "ckpt/./present",
        "ckpt/present/",
    ],
)
def test_resolve_crafted_ckpt_ids_are_unknown(crafted: str) -> None:
    """The id is compared to scanned ids, never turned into a path: anything but an exact id is 404."""
    _checkpoint("present.safetensors", _SDXL_KEY)
    _checkpoint("sub/real.safetensors", _SDXL_KEY)
    _checkpoint("anime/waifu.safetensors", _SDXL_KEY)

    with pytest.raises(ImageModelUnknown):
        resolve_image_model(crafted, selection_model=None)


def test_resolve_user_ckpt_that_is_not_there_is_unknown() -> None:
    _checkpoint("present.safetensors", _SDXL_KEY)
    with pytest.raises(ImageModelUnknown):
        resolve_image_model("ckpt/absent", selection_model=None)


def test_resolve_user_ckpt_of_unknown_arch_is_unknown() -> None:
    """A checkpoint whose family cannot be sniffed gets no api id, so it cannot be named."""
    _checkpoint("flux.safetensors", "double_blocks.0.x")
    _checkpoint("junk.safetensors", None)
    for stem in ("flux", "junk"):
        with pytest.raises(ImageModelUnknown):
            resolve_image_model(f"ckpt/{stem}", selection_model=None)


def test_resolve_catalog_owned_checkpoint_has_no_ckpt_id() -> None:
    """The SD 1.5 weights belong to the catalog entry; they are not also a user checkpoint."""
    _install("sd-1.5-pruned-emaonly")
    with pytest.raises(ImageModelUnknown):
        resolve_image_model("ckpt/v1-5-pruned-emaonly", selection_model=None)


def test_resolve_unknown_404_lists_available() -> None:
    _install("sd-1.5-pruned-emaonly")
    _checkpoint("mine.safetensors", _SDXL_KEY)

    with pytest.raises(ImageModelUnknown) as exc:
        resolve_image_model("not-a-model", selection_model=None)

    err = exc.value
    assert (err.code, err.status) == ("image.model_unknown", 404)
    assert err.details["model"] == "not-a-model"
    assert err.details["available"] == ["sd-1.5-pruned-emaonly", "ckpt/mine"]
    assert "sd-1.5-pruned-emaonly" in err.message
    assert "ckpt/mine" in err.message


def test_resolve_unknown_with_nothing_installed_says_so() -> None:
    with pytest.raises(ImageModelUnknown) as exc:
        resolve_image_model("not-a-model", selection_model=None)
    assert exc.value.details["available"] == []
    assert "no image models are installed" in exc.value.message


def test_resolve_falls_back_to_selection() -> None:
    _install("sd-1.5-pruned-emaonly")
    for empty in (None, "", "   "):
        resolved = resolve_image_model(empty, selection_model="sd-1.5-pruned-emaonly")
        assert resolved.id == "sd-1.5-pruned-emaonly"


def test_resolve_explicit_model_wins_over_selection() -> None:
    _install("sd-1.5-pruned-emaonly")
    _install("sdxl-turbo")
    resolved = resolve_image_model("sdxl-turbo", selection_model="sd-1.5-pruned-emaonly")
    assert resolved.id == "sdxl-turbo"


def test_resolve_selection_goes_through_the_same_checks() -> None:
    """A bound-but-not-installed model is a 409, exactly as if the client had named it."""
    with pytest.raises(ImageModelNotInstalled):
        resolve_image_model(None, selection_model="sdxl-turbo")


def test_resolve_required_422() -> None:
    for model, selection in ((None, None), ("", ""), ("  ", None), (None, "  ")):
        with pytest.raises(ImageModelRequired) as exc:
            resolve_image_model(model, selection_model=selection)
        assert (exc.value.code, exc.value.status) == ("image.model_required", 422)


def test_renderable_ids_are_installed_catalog_models_then_user_checkpoints() -> None:
    _install("sdxl-turbo")
    _install("sd-1.5-pruned-emaonly")
    _install("wan22-t2v")  # installed, but the API cannot render it
    # sdxl-lightning has only its base checkpoint: not complete.
    base = CATALOG["sdxl-lightning"].components[0]
    base_path = component_path(base, paths.models_root())
    base_path.parent.mkdir(parents=True, exist_ok=True)
    base_path.write_bytes(b"x")
    _checkpoint("zeta.safetensors", _SD15_KEY)
    _checkpoint("alpha.safetensors", _SDXL_KEY)
    _checkpoint("sub/beta.safetensors", _SDXL_KEY)
    _checkpoint("mystery.safetensors", None)

    assert renderable_ids() == [
        "sdxl-turbo",
        "sd-1.5-pruned-emaonly",
        "ckpt/alpha",
        "ckpt/sub/beta",
        "ckpt/zeta",
    ]


def test_renderable_ids_empty_when_nothing_is_installed() -> None:
    assert renderable_ids() == []
