"""The image-model catalog: typed weight files, workflow templates, install state (ADR-0037).

An image model is a set of typed weight files (*components*) that one workflow
template loads together; the dashboard, the installer and the generation route
all read this table. Install state is never stored: it is derived from which
component files exist under :func:`hal0.comfyui.paths.models_root`, so a file an
operator adds or removes by hand shows up the next time anyone asks.

Two rules keep the table honest:

* A model's components are exactly the weight files its template references
  (:func:`weight_refs`). The catalog tests enforce it against every shipped
  template.
* Two components with the same destination share one source, so a shared text
  encoder or VAE is one file with one origin. The shared components below are
  module constants for that reason.

The fp8 builds of the video models reuse their full-precision sibling's template
and rename the weight files at render time (:attr:`ImageModel.file_substitutions`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from hal0.comfyui import paths

# ── data model ──────────────────────────────────────────────────────────────


class ComponentKind(StrEnum):
    """What a weight file is; decides which ComfyUI model subdirectory holds it."""

    checkpoint = "checkpoint"
    diffusion_model = "diffusion_model"
    text_encoder = "text_encoder"
    vae = "vae"
    lora = "lora"
    upscaler = "upscaler"
    clip_vision = "clip_vision"
    latent_upscaler = "latent_upscaler"

    @property
    def subdir(self) -> str:
        """The directory name under ``models_root()``, as ``paths.SUBDIRS`` names it."""
        return paths.SUBDIRS[_KIND_MODEL_TYPE[self.value]]


# Component kind -> the ComfyUI model-type key in ``paths.SUBDIRS``, which owns the directory names.
_KIND_MODEL_TYPE: Mapping[str, str] = MappingProxyType(
    {
        "checkpoint": "checkpoints",
        "diffusion_model": "diffusion_models",
        "text_encoder": "text_encoders",
        "vae": "vae",
        "lora": "loras",
        "upscaler": "upscale_models",
        "clip_vision": "clip_vision",
        "latent_upscaler": "latent_upscale_models",
    }
)


@dataclass(frozen=True)
class Component:
    """One weight file: where it lands, where it comes from, roughly how big it is.

    ``relpath`` is the path under the kind's subdirectory and may be nested (Wan's
    LoRAs live in a directory of their own). The source is a Hugging Face file,
    ``hf_path`` within ``hf_repo``; it may differ from ``relpath`` when the file
    is stored under another name or in a subdirectory of the repo.
    """

    kind: ComponentKind
    relpath: str
    approx_gb: float
    hf_repo: str
    hf_path: str

    @property
    def dest(self) -> Path:
        """Where the file lives on disk.

        Reads the models root, which re-reads the config; code that resolves many
        components in one pass takes the root once and calls :func:`component_path`.
        """
        return component_path(self, paths.models_root())

    @property
    def source_key(self) -> str:
        """A string that is equal for two components exactly when they share a source."""
        return f"hf://{self.hf_repo}/{self.hf_path}"


def component_path(component: Component, root: Path) -> Path:
    """Where ``component`` lands under the models tree ``root`` (``paths.models_root()``)."""
    return root / component.kind.subdir / component.relpath


@dataclass(frozen=True)
class ImageModel:
    """One installable image model.

    ``task`` is ``txt2img``, ``img2img``, ``txt2video``, ``img2video`` or ``upscale``.
    ``template`` is a workflow stem under ``hal0.providers.workflows``. ``sizes`` are
    ``WxH`` strings with the native size first; they are empty for models the API
    never renders, whose editor workflow sets its own size.
    ``file_substitutions`` maps a weight filename in the template to the filename
    this model loads instead; it is how a precision variant reuses its sibling's
    graph. It is exposed read-only and left out of the hash.
    """

    id: str
    label: str
    task: str
    components: tuple[Component, ...]
    template: str
    api_renderable: bool
    sizes: tuple[str, ...]
    est_seconds: int
    license: str
    notes: str = ""
    file_substitutions: Mapping[str, str] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # Copy then freeze: the caller's dict can no longer change the model.
        object.__setattr__(
            self, "file_substitutions", MappingProxyType(dict(self.file_substitutions))
        )

    @property
    def approx_gb(self) -> float:
        """Total download size of every component."""
        return round(sum(c.approx_gb for c in self.components), 1)

    @property
    def workflow_filename(self) -> str:
        """The name the workflow is provisioned under, so variants of one template never collide."""
        return f"{self.id}.json"


@dataclass(frozen=True)
class InstallState:
    """How much of a model is on disk, and what is still missing."""

    state: Literal["none", "partial", "complete"]
    missing: tuple[Component, ...]
    missing_gb: float


# ── templates ───────────────────────────────────────────────────────────────

_WEIGHT_SUFFIXES = (".safetensors", ".pth", ".ckpt", ".pt", ".gguf")

# The pointer contract of an API-renderable template. ``_meta.params`` maps a parameter name
# to ``node:<id>.inputs.<field>``, the graph input that parameter patches, so the generation
# route sets prompts, sizes and seeds without knowing which model a graph belongs to.
# ``_meta.defaults`` holds the graph's own ``steps`` and ``cfg`` for requests that omit them.
REQUIRED_PARAMS = (
    "positive_prompt",
    "width",
    "height",
    "seed",
    "steps",
    "cfg",
    "filename_prefix",
)
OPTIONAL_PARAMS = ("negative_prompt", "batch_size", "ckpt_name")

# Templates that load a caller-chosen checkpoint (``ckpt_name`` is patched), by model family.
USER_CKPT_TEMPLATES: Mapping[str, str] = MappingProxyType(
    {"sdxl": "sdxl_simple", "sd15": "sd15_simple"}
)


_POINTER = re.compile(r"node:(?P<node>\w+)\.inputs\.(?P<field>\w+)")


def parse_pointer(pointer: str) -> tuple[str, str]:
    """``(node id, input field)`` of a ``node:<id>.inputs.<field>`` pointer.

    Raises :class:`ValueError` when ``pointer`` is not in that grammar.
    """
    match = _POINTER.fullmatch(pointer)
    if match is None:
        raise ValueError(f"malformed pointer {pointer!r}: expected node:<id>.inputs.<field>")
    return match["node"], match["field"]


def load_template(stem: str) -> dict[str, Any]:
    """The workflow template ``stem`` from package resources, ``_meta`` included.

    Raises :class:`FileNotFoundError` when no such template ships and
    :class:`ValueError` when the file is not a JSON object.
    """
    text = (
        resources.files("hal0.providers.workflows")
        .joinpath(f"{stem}.json")
        .read_text(encoding="utf-8")
    )
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"workflow template {stem!r} root is not an object")
    return data


def _graph_nodes(graph: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """Every node of a prompt-format graph; ``_meta`` and other non-node keys are skipped."""
    for key, node in graph.items():
        if key != "_meta" and isinstance(node, dict) and isinstance(node.get("inputs"), dict):
            yield node


def weight_refs(graph: Mapping[str, Any]) -> set[tuple[str, str]]:
    """``(node class_type, value)`` for every weight-file input in ``graph``.

    A weight file is a string input ending in ``.safetensors``, ``.pth``, ``.ckpt``,
    ``.pt`` or ``.gguf``. Only graph nodes are scanned, never ``_meta``.
    """
    return {
        (str(node.get("class_type", "")), value)
        for node in _graph_nodes(graph)
        for value in node["inputs"].values()
        if isinstance(value, str) and value.endswith(_WEIGHT_SUFFIXES)
    }


def template_weight_refs(stem: str) -> set[tuple[str, str]]:
    """:func:`weight_refs` of the shipped template ``stem``."""
    return weight_refs(load_template(stem))


def render_template(model: ImageModel) -> dict[str, Any]:
    """The model's template with its ``file_substitutions`` applied.

    Every string input value that equals a substitution key is replaced; ``_meta``
    is kept as shipped. The result is a fresh dict the caller may mutate.
    """
    graph = load_template(model.template)
    subs = model.file_substitutions
    if subs:
        for node in _graph_nodes(graph):
            inputs = node["inputs"]
            for name, value in list(inputs.items()):
                if isinstance(value, str) and value in subs:
                    inputs[name] = subs[value]
    return graph


# ── the models ──────────────────────────────────────────────────────────────


def _hf(
    kind: ComponentKind, relpath: str, approx_gb: float, repo: str, path: str = ""
) -> Component:
    """A Hugging Face component; ``path`` defaults to ``relpath`` (the file is not renamed)."""
    return Component(kind, relpath, approx_gb, hf_repo=repo, hf_path=path or relpath)


_CKPT = ComponentKind.checkpoint
_UNET = ComponentKind.diffusion_model
_TEXT = ComponentKind.text_encoder
_VAE = ComponentKind.vae
_LORA = ComponentKind.lora

# SDXL family. The VAE lands under a name of our own; the repo file is generic.
_SDXL_BASE = _hf(
    _CKPT, "sd_xl_base_1.0.safetensors", 6.9, "stabilityai/stable-diffusion-xl-base-1.0"
)
_SDXL_LIGHTNING_LORA = _hf(
    _LORA, "sdxl_lightning_8step_lora.safetensors", 0.4, "ByteDance/SDXL-Lightning"
)
_SDXL_VAE = _hf(
    _VAE,
    "sdxl_vae_fp16_fix.safetensors",
    0.3,
    "madebyollin/sdxl-vae-fp16-fix",
    "diffusion_pytorch_model.safetensors",
)

# Qwen-Image and Qwen-Image-Edit share the text encoder and VAE; HunyuanVideo
# 1.5 loads the same text encoder, so it points at the same source.
_QWEN_REPO = "Comfy-Org/Qwen-Image_ComfyUI"
_QWEN_TEXT_ENCODER = _hf(
    _TEXT,
    "qwen_2.5_vl_7b_fp8_scaled.safetensors",
    9.4,
    _QWEN_REPO,
    "split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors",
)
_QWEN_VAE = _hf(
    _VAE,
    "qwen_image_vae.safetensors",
    0.3,
    _QWEN_REPO,
    "split_files/vae/qwen_image_vae.safetensors",
)
_QWEN_IMAGE = _hf(
    _UNET,
    "qwen_image_2512_bf16.safetensors",
    40.9,
    _QWEN_REPO,
    "split_files/diffusion_models/qwen_image_2512_bf16.safetensors",
)
_QWEN_IMAGE_LORA = _hf(
    _LORA,
    "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors",
    0.8,
    "lightx2v/Qwen-Image-2512-Lightning",
)
_QWEN_EDIT = _hf(
    _UNET,
    "qwen_image_edit_2511_bf16.safetensors",
    40.9,
    "Comfy-Org/Qwen-Image-Edit_ComfyUI",
    "split_files/diffusion_models/qwen_image_edit_2511_bf16.safetensors",
)
_QWEN_EDIT_LORA = _hf(
    _LORA,
    "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
    0.8,
    "lightx2v/Qwen-Image-Edit-2511-Lightning",
)

# LTX-2: one checkpoint (fp8 or bf16) serves the video, audio and text-encoder loaders.
_LTX2_FP8 = _hf(_CKPT, "ltx-2-19b-dev-fp8.safetensors", 27.1, "Lightricks/LTX-2")
_LTX2_BF16 = _hf(_CKPT, "ltx-2-19b-dev.safetensors", 43.3, "Lightricks/LTX-2")
_LTX2_TEXT_ENCODER = _hf(
    _TEXT,
    "gemma_3_12B_it_fp4_mixed.safetensors",
    9.4,
    "Comfy-Org/ltx-2",
    "split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors",
)
_LTX2_UPSCALER = _hf(
    ComponentKind.latent_upscaler,
    "ltx-2-spatial-upscaler-x2-1.0.safetensors",
    1.0,
    "Lightricks/LTX-2",
)
_LTX2_DISTILLED_LORA = _hf(
    _LORA, "ltx-2-19b-distilled-lora-384.safetensors", 7.7, "Lightricks/LTX-2"
)
_LTX2_FP8_RENAMES = {"ltx-2-19b-dev.safetensors": "ltx-2-19b-dev-fp8.safetensors"}

# HunyuanVideo 1.5: the 4-step LoRA is the T2V one for both graphs.
_HUNYUAN_REPO = "Comfy-Org/HunyuanVideo_1.5_repackaged"
_HUNYUAN_BYT5 = _hf(
    _TEXT,
    "byt5_small_glyphxl_fp16.safetensors",
    0.4,
    _HUNYUAN_REPO,
    "split_files/text_encoders/byt5_small_glyphxl_fp16.safetensors",
)
_HUNYUAN_VAE = _hf(
    _VAE,
    "hunyuanvideo15_vae_fp16.safetensors",
    2.5,
    _HUNYUAN_REPO,
    "split_files/vae/hunyuanvideo15_vae_fp16.safetensors",
)
_HUNYUAN_LORA = _hf(
    _LORA,
    "hunyuanvideo1.5_t2v_480p_lightx2v_4step_lora_rank_32_bf16.safetensors",
    0.3,
    _HUNYUAN_REPO,
    "split_files/loras/hunyuanvideo1.5_t2v_480p_lightx2v_4step_lora_rank_32_bf16.safetensors",
)
_HUNYUAN_CLIP_VISION = _hf(
    ComponentKind.clip_vision,
    "sigclip_vision_patch14_384.safetensors",
    0.9,
    "Comfy-Org/sigclip_vision_384",
)


def _hunyuan_unet(task: Literal["t2v", "i2v"]) -> Component:
    name = f"hunyuanvideo1.5_720p_{task}_fp16.safetensors"
    return _hf(_UNET, name, 16.7, _HUNYUAN_REPO, f"split_files/diffusion_models/{name}")


# Wan 2.2 14B: a high-noise and a low-noise diffusion model per task, each in fp8
# (scaled) and fp16. The fp8 text encoder comes from the Wan 2.1 repo.
_WAN22_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
_WAN_LORA_REPO = "lightx2v/Wan2.2-Lightning"
_WAN_VAE = _hf(
    _VAE,
    "wan_2.1_vae.safetensors",
    0.3,
    _WAN22_REPO,
    "split_files/vae/wan_2.1_vae.safetensors",
)
_WAN_UMT5_FP8 = _hf(
    _TEXT,
    "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    6.7,
    "Comfy-Org/Wan_2.1_ComfyUI_repackaged",
    "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
)
_WAN_UMT5_FP16 = _hf(
    _TEXT,
    "umt5_xxl_fp16.safetensors",
    11.4,
    _WAN22_REPO,
    "split_files/text_encoders/umt5_xxl_fp16.safetensors",
)


def _wan_unet(
    task: Literal["t2v", "i2v"], noise: str, precision: Literal["fp8", "fp16"]
) -> Component:
    suffix = "fp8_scaled" if precision == "fp8" else "fp16"
    name = f"wan2.2_{task}_{noise}_noise_14B_{suffix}.safetensors"
    return _hf(
        _UNET,
        name,
        14.3 if precision == "fp8" else 28.6,
        _WAN22_REPO,
        f"split_files/diffusion_models/{name}",
    )


# task key -> (catalog task, label noun, template stem, LoRA directory)
_WAN_TASKS = {
    "t2v": (
        "txt2video",
        "Text to Video",
        "Wan2.2-T2V-A14B-FP16-4steps-lora-rank64-Seko-V2",
        "Wan2.2-T2V-A14B-4steps-lora-rank64-Seko-V2.0",
    ),
    "i2v": (
        "img2video",
        "Image to Video",
        "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1-FP16",
        "Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1",
    ),
}


def _wan_model(
    task: Literal["t2v", "i2v"], precision: Literal["fp8", "fp16"], est_seconds: int
) -> ImageModel:
    """A Wan 2.2 entry. Both precisions share the fp16 template; fp8 renames its weights."""
    catalog_task, noun, template, lora_dir = _WAN_TASKS[task]
    fp8 = precision == "fp8"
    renames: dict[str, str] = {}
    if fp8:
        renames["umt5_xxl_fp16.safetensors"] = _WAN_UMT5_FP8.relpath
        for noise in ("high", "low"):
            renames[_wan_unet(task, noise, "fp16").relpath] = _wan_unet(task, noise, "fp8").relpath
    return ImageModel(
        id=f"wan22-{task}" if fp8 else f"wan22-{task}-fp16",
        label=f"Wan 2.2 {noun} ({precision})",
        task=catalog_task,
        components=(
            _wan_unet(task, "high", precision),
            _wan_unet(task, "low", precision),
            _WAN_UMT5_FP8 if fp8 else _WAN_UMT5_FP16,
            _WAN_VAE,
            _hf(_LORA, f"{lora_dir}/high_noise_model.safetensors", 1.2, _WAN_LORA_REPO),
            _hf(_LORA, f"{lora_dir}/low_noise_model.safetensors", 1.2, _WAN_LORA_REPO),
        ),
        template=template,
        api_renderable=False,
        sizes=(),
        est_seconds=est_seconds,
        license="Apache-2.0",
        notes="Seko 4-step Lightning LoRAs on both the high- and low-noise models."
        + (" The fp8 build is the smaller download." if fp8 else ""),
        file_substitutions=renames,
    )


_SDXL_SIZES = (
    "1024x1024",
    "1152x896",
    "896x1152",
    "1216x832",
    "832x1216",
    "1344x768",
    "768x1344",
)
_SD15_SIZES = ("512x512", "512x768", "768x512")
_QWEN_SIZES = ("1328x1328", "1664x928", "928x1664", "1472x1140", "1140x1472")

# Insertion order is display order, grouped by task.
CATALOG: dict[str, ImageModel] = {
    m.id: m
    for m in (
        # ── txt2img ──
        ImageModel(
            id="sdxl-lightning",
            label="SDXL Lightning (8-step)",
            task="txt2img",
            components=(_SDXL_BASE, _SDXL_LIGHTNING_LORA, _SDXL_VAE),
            template="SDXL-Lightning-8step",
            api_renderable=True,
            sizes=_SDXL_SIZES,
            est_seconds=10,
            license="CreativeML-OpenRAIL++",
            notes="SDXL base with ByteDance's 8-step Lightning LoRA and the fp16-fix VAE.",
        ),
        ImageModel(
            id="sdxl-turbo",
            label="SDXL Turbo",
            task="txt2img",
            components=(
                _hf(
                    _CKPT,
                    "sd_xl_turbo_1.0_fp16.safetensors",
                    6.9,
                    "stabilityai/sdxl-turbo",
                ),
            ),
            template="sdxl_turbo_simple",
            api_renderable=True,
            sizes=_SD15_SIZES,
            est_seconds=3,
            license="SAI-NC-Research-Community",
            notes="Distilled 1-4 step SDXL. Research-only licence.",
        ),
        ImageModel(
            id="sd-1.5-pruned-emaonly",
            label="Stable Diffusion 1.5",
            task="txt2img",
            components=(
                _hf(
                    _CKPT,
                    "v1-5-pruned-emaonly.safetensors",
                    4.3,
                    "runwayml/stable-diffusion-v1-5",
                ),
            ),
            template="sd15_simple",
            api_renderable=True,
            sizes=_SD15_SIZES,
            est_seconds=5,
            license="CreativeML-Open-RAIL-M",
            notes="Pruned EMA-only checkpoint; runs on small GPUs. Quality drops above 768.",
        ),
        ImageModel(
            id="qwen-image-lightning",
            label="Qwen-Image (4-step Lightning)",
            task="txt2img",
            components=(_QWEN_IMAGE, _QWEN_TEXT_ENCODER, _QWEN_VAE, _QWEN_IMAGE_LORA),
            template="Qwen-Image-2512-BF16-4-Step-LoRA",
            api_renderable=True,
            sizes=_QWEN_SIZES,
            est_seconds=75,
            license="Apache-2.0",
            notes="Qwen-Image 2512 in bf16 with the 4-step Lightning LoRA.",
        ),
        ImageModel(
            id="qwen-image",
            label="Qwen-Image (20-step)",
            task="txt2img",
            components=(_QWEN_IMAGE, _QWEN_TEXT_ENCODER, _QWEN_VAE),
            template="Qwen-Image-2512-BF16-20-Steps",
            api_renderable=True,
            sizes=_QWEN_SIZES,
            est_seconds=359,
            license="Apache-2.0",
            notes="Qwen-Image 2512 in bf16, full 20-step sampling.",
        ),
        # ── img2img ──
        ImageModel(
            id="qwen-image-edit-lightning",
            label="Qwen-Image Edit (4-step Lightning)",
            task="img2img",
            components=(_QWEN_EDIT, _QWEN_TEXT_ENCODER, _QWEN_VAE, _QWEN_EDIT_LORA),
            template="Qwen-Image-Edit-2511-BF16-4-Step-LoRA",
            api_renderable=False,
            sizes=(),
            est_seconds=113,
            license="Apache-2.0",
            notes="Qwen-Image-Edit 2511 in bf16 with the 4-step Lightning LoRA.",
        ),
        ImageModel(
            id="qwen-image-edit",
            label="Qwen-Image Edit (20-step)",
            task="img2img",
            components=(_QWEN_EDIT, _QWEN_TEXT_ENCODER, _QWEN_VAE),
            template="Qwen-Image-Edit-2511-BF16-20-Steps",
            api_renderable=False,
            sizes=(),
            est_seconds=667,
            license="Apache-2.0",
            notes="Qwen-Image-Edit 2511 in bf16, full 20-step sampling.",
        ),
        # ── txt2video ──
        ImageModel(
            id="ltx2-t2v",
            label="LTX-2 Text to Video (fp8)",
            task="txt2video",
            components=(_LTX2_FP8, _LTX2_TEXT_ENCODER, _LTX2_UPSCALER, _LTX2_DISTILLED_LORA),
            template="LTX2-T2V-BF16",
            api_renderable=False,
            sizes=(),
            est_seconds=615,
            license="LTX-2-Community",
            notes="Distilled LoRA and a 2x spatial upscaler. The fp8 build is the smaller download.",
            file_substitutions=_LTX2_FP8_RENAMES,
        ),
        ImageModel(
            id="ltx2-t2v-bf16",
            label="LTX-2 Text to Video (bf16)",
            task="txt2video",
            components=(_LTX2_BF16, _LTX2_TEXT_ENCODER, _LTX2_UPSCALER, _LTX2_DISTILLED_LORA),
            template="LTX2-T2V-BF16",
            api_renderable=False,
            sizes=(),
            est_seconds=615,
            license="LTX-2-Community",
            notes="Distilled LoRA with a 2x spatial upscaler.",
        ),
        ImageModel(
            id="hunyuan15-t2v",
            label="HunyuanVideo 1.5 Text to Video (720p)",
            task="txt2video",
            components=(
                _hunyuan_unet("t2v"),
                _QWEN_TEXT_ENCODER,
                _HUNYUAN_BYT5,
                _HUNYUAN_VAE,
                _HUNYUAN_LORA,
            ),
            template="Hunyuan-Video-1.5_720p_t2v-4-step-lora",
            api_renderable=False,
            sizes=(),
            est_seconds=929,
            license="Tencent-Hunyuan-Community",
            notes="720p fp16 with the lightx2v 4-step LoRA.",
        ),
        _wan_model("t2v", "fp8", 2007),
        _wan_model("t2v", "fp16", 2007),
        # ── img2video ──
        ImageModel(
            id="ltx2-i2v",
            label="LTX-2 Image to Video (fp8)",
            task="img2video",
            components=(_LTX2_FP8, _LTX2_TEXT_ENCODER, _LTX2_UPSCALER, _LTX2_DISTILLED_LORA),
            template="LTX2-I2V-BF16",
            api_renderable=False,
            sizes=(),
            est_seconds=616,
            license="LTX-2-Community",
            notes="Distilled LoRA and a 2x spatial upscaler. The fp8 build is the smaller download.",
            file_substitutions=_LTX2_FP8_RENAMES,
        ),
        ImageModel(
            id="ltx2-i2v-bf16",
            label="LTX-2 Image to Video (bf16)",
            task="img2video",
            components=(_LTX2_BF16, _LTX2_TEXT_ENCODER, _LTX2_UPSCALER, _LTX2_DISTILLED_LORA),
            template="LTX2-I2V-BF16",
            api_renderable=False,
            sizes=(),
            est_seconds=616,
            license="LTX-2-Community",
            notes="Distilled LoRA with a 2x spatial upscaler.",
        ),
        ImageModel(
            id="hunyuan15-i2v",
            label="HunyuanVideo 1.5 Image to Video (720p)",
            task="img2video",
            components=(
                _hunyuan_unet("i2v"),
                _QWEN_TEXT_ENCODER,
                _HUNYUAN_BYT5,
                _HUNYUAN_VAE,
                _HUNYUAN_CLIP_VISION,
                _HUNYUAN_LORA,
            ),
            template="Hunyuan-Video-1.5_720p_i2v-4-step-lora",
            api_renderable=False,
            sizes=(),
            est_seconds=947,
            license="Tencent-Hunyuan-Community",
            notes="720p fp16 with the lightx2v 4-step LoRA and the SigCLIP vision encoder.",
        ),
        _wan_model("i2v", "fp8", 2029),
        _wan_model("i2v", "fp16", 2029),
        # ── upscale ──
        ImageModel(
            id="esrgan-4x",
            label="ESRGAN 4x Upscale",
            task="upscale",
            components=(
                _hf(
                    ComponentKind.upscaler,
                    "4x-UltraSharp.pth",
                    0.1,
                    "lokCX/4x-Ultrasharp",
                ),
            ),
            template="ESRGAN-4x-Upscale",
            api_renderable=False,
            sizes=(),
            est_seconds=10,
            license="CC-BY-NC-SA-4.0",
            notes=(
                "4x-UltraSharp, served from a public mirror that carries no licence "
                "of its own; the upstream release is CC BY-NC-SA 4.0."
            ),
        ),
    )
}


# ── queries ─────────────────────────────────────────────────────────────────


def get_model(model_id: str) -> ImageModel | None:
    """The catalog entry for ``model_id``, or ``None``."""
    return CATALOG.get(model_id)


def install_path(model_id: str) -> str:
    """The API path that installs ``model_id`` (``POST``), for a client to call."""
    return f"/api/comfyui/models/{model_id}/install"


def install_route(model_id: str) -> str:
    """The install call as an error message or ``details`` names it: ``POST <path>``."""
    return f"POST {install_path(model_id)}"


def recommended_model_id(pool_gb: float | None) -> str:
    """The starter model that suits a memory pool of ``pool_gb`` gigabytes.

    The pool is the memory the box can load a model into, as
    :func:`hal0.comfyui.picker.image_pool_gb` computes it: the unified memory when the box
    has it, else system RAM, capped at the guest's RAM limit. ``None`` means the pool is
    unknown (the hardware file is unreadable, or the box was never probed): the middle
    pick, which any box that can run ComfyUI at all can take. Every other catalog model
    stays installable; this only picks the one to highlight.
    """
    if pool_gb is None:
        return "sdxl-lightning"
    if pool_gb >= 96:
        return "qwen-image-lightning"
    if pool_gb >= 16:
        return "sdxl-lightning"
    return "sd-1.5-pruned-emaonly"


def install_state(model: ImageModel, root: Path | None = None) -> InstallState:
    """``complete`` when every component file exists, ``none`` when none does, else ``partial``.

    ``root`` is the models tree; a caller checking several models passes it once.
    """
    root = root or paths.models_root()
    missing = tuple(c for c in model.components if not component_path(c, root).is_file())
    if not missing:
        state: Literal["none", "partial", "complete"] = "complete"
    elif len(missing) == len(model.components):
        state = "none"
    else:
        state = "partial"
    return InstallState(state, missing, round(sum(c.approx_gb for c in missing), 1))


def models_using(component: Component, root: Path | None = None) -> list[str]:
    """Ids of the catalog models whose components include a file at ``component.dest``.

    ``root`` is the models tree; a caller checking several components passes it once.
    """
    root = root or paths.models_root()
    dest = component_path(component, root)
    return [
        m.id for m in CATALOG.values() if any(component_path(c, root) == dest for c in m.components)
    ]
