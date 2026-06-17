"""Estimate a ComfyUI render's GPU memory footprint from its prompt JSON.

Pure + dependency-free: walk the API-format prompt for model-loading nodes,
map each to its on-disk model file, sum unique files, and apply a peak
multiplier (higher when video nodes imply large latent tensors). Conservative
by design — an unrecognised model contributes a default rather than zero, so an
unknown workflow biases toward "needs the whole GPU", never toward a too-rosy
coexist decision.
"""

from __future__ import annotations

import os

_GiB = 1024**3

#: class_type → ((input_field, models_subdir), ...). Covers the loaders the
#: curated hal0 workflows use; extend as new model families land.
LOADER_MODEL_INPUTS: dict[str, tuple[tuple[str, str], ...]] = {
    "CheckpointLoaderSimple": (("ckpt_name", "checkpoints"),),
    "CheckpointLoader": (("ckpt_name", "checkpoints"),),
    "UNETLoader": (("unet_name", "diffusion_models"),),
    "UNETLoaderGGUF": (("unet_name", "diffusion_models"),),
    "VAELoader": (("vae_name", "vae"),),
    "CLIPLoader": (("clip_name", "text_encoders"),),
    "CLIPLoaderGGUF": (("clip_name", "text_encoders"),),
    "DualCLIPLoader": (("clip_name1", "text_encoders"), ("clip_name2", "text_encoders")),
    "LoraLoader": (("lora_name", "loras"),),
    "LoraLoaderModelOnly": (("lora_name", "loras"),),
    "ControlNetLoader": (("control_net_name", "controlnet"),),
    "CLIPVisionLoader": (("clip_name", "clip_vision"),),
}

#: Node class_types that imply large video latents → use the video peak factor.
VIDEO_NODE_TYPES: frozenset[str] = frozenset(
    {
        "SaveWEBM",
        "VHS_VideoCombine",
        "SaveVideo",
        "WanImageToVideo",
        "WanVideoSampler",
        "LTXVConditioning",
        "EmptyHunyuanLatentVideo",
        "EmptyLTXVLatentVideo",
    }
)


def iter_model_files(prompt: dict) -> list[tuple[str, str]]:
    """Return (subdir, filename) for every model referenced by a loader node."""
    out: list[tuple[str, str]] = []
    if not isinstance(prompt, dict):
        return out
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        mapping = LOADER_MODEL_INPUTS.get(node.get("class_type", ""))
        if not mapping:
            continue
        inputs = node.get("inputs") or {}
        for field, subdir in mapping:
            name = inputs.get(field)
            if isinstance(name, str) and name:
                out.append((subdir, name))
    return out


def _has_video_node(prompt: dict) -> bool:
    return any(
        isinstance(n, dict) and n.get("class_type") in VIDEO_NODE_TYPES for n in prompt.values()
    )


def estimate_footprint_gb(
    prompt: dict,
    model_dir: str,
    *,
    peak_factor: float = 1.3,
    video_peak_factor: float = 1.6,
    unknown_model_gb: float = 8.0,
) -> float:
    """Conservative GiB estimate of a render's peak GPU footprint."""
    seen: set[tuple[str, str]] = set()
    raw_gb = 0.0
    for subdir, name in iter_model_files(prompt):
        key = (subdir, name)
        if key in seen:
            continue
        seen.add(key)
        path = os.path.join(model_dir, subdir, name)
        try:
            raw_gb += os.path.getsize(path) / _GiB
        except OSError:
            raw_gb += unknown_model_gb
    factor = video_peak_factor if _has_video_node(prompt) else peak_factor
    return raw_gb * factor
