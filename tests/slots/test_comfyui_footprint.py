from __future__ import annotations

import os

from hal0.slots.comfyui_footprint import (
    estimate_footprint_gb,
    iter_model_files,
)

_GiB = 1024**3


def _write(path: str, size_bytes: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size_bytes)


def test_iter_model_files_extracts_loader_inputs() -> None:
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    files = set(iter_model_files(prompt))
    assert ("checkpoints", "sdxl.safetensors") in files
    assert ("vae", "sdxl_vae.safetensors") in files
    # non-loader nodes contribute nothing
    assert len(files) == 2


def test_estimate_sums_unique_files_with_peak_factor(tmp_path) -> None:
    model_dir = str(tmp_path)
    _write(os.path.join(model_dir, "checkpoints", "sdxl.safetensors"), 7 * _GiB)
    _write(os.path.join(model_dir, "vae", "sdxl_vae.safetensors"), 1 * _GiB)
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
    }
    # (7 + 1) GiB * 1.3 = 10.4
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.3)
    assert abs(est - 10.4) < 0.05


def test_estimate_uses_video_peak_factor_when_video_node_present(tmp_path) -> None:
    model_dir = str(tmp_path)
    _write(os.path.join(model_dir, "diffusion_models", "wan_hi.safetensors"), 13 * _GiB)
    _write(os.path.join(model_dir, "diffusion_models", "wan_lo.safetensors"), 13 * _GiB)
    prompt = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan_hi.safetensors"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan_lo.safetensors"}},
        "3": {"class_type": "SaveWEBM", "inputs": {}},
    }
    # 26 GiB * 1.6 = 41.6 (video factor, not 1.3)
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.3, video_peak_factor=1.6)
    assert abs(est - 41.6) < 0.05


def test_unknown_or_missing_model_uses_conservative_default(tmp_path) -> None:
    model_dir = str(tmp_path)
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "nope.safetensors"}},
    }
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.0, unknown_model_gb=8.0)
    assert abs(est - 8.0) < 0.01
