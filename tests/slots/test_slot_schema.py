"""Every static seed slot TOML validates against SlotConfig and matches the
spec-p3-brain §5 + spec §5.1 + §5.4 mapping.

Per docs/superpowers/specs/2026-07-20-seeded-profile-rework-design.md §5.1 +
§5.4.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import SlotConfig

SLOTS_DIR = Path(__file__).resolve().parents[2] / "installer/etc-hal0/slots"

EXPECTED_MAPPING = {
    # slot name: (port, device, profile)
    "brain":     (8089, "gpu-vulkan",  "brain"),
    "agent":     (8081, "gpu-vulkan",  "chadrock-moe"),
    "utility":   (8090, "gpu-vulkan",  "chat"),
    "flm":       (8088, "npu",         "flm"),
    "img":       (8188, "gpu-rocm",    "comfyui"),
    "qwen3tts":  (8095, "gpu-rocm",    "qwen3-tts"),
    "tts":       (8085, "cpu",         "kokoro"),    # port drift fix (was 8084)
    "rerank":    (8086, "gpu-vulkan",  "reranking"), # port drift fix (was 8083)
    "coder":     (8082, "gpu-vulkan",  "coding"),    # NEW static seed (Task 5)
    "embed":     (8083, "gpu-vulkan",  "embedding"), # NEW static seed (Task 5)
}


def _load_slot(name: str) -> SlotConfig:
    slot_path = SLOTS_DIR / f"{name}.toml"
    if not slot_path.is_file():
        raise FileNotFoundError(f"missing slot TOML: {slot_path}")
    return SlotConfig.model_validate(tomllib.loads(slot_path.read_text()))


@pytest.mark.parametrize("slot_name,expected", list(EXPECTED_MAPPING.items()))
def test_static_seed_slot_matches_mapping(slot_name: str, expected: tuple) -> None:
    port, device, profile = expected
    slot_path = SLOTS_DIR / f"{slot_name}.toml"
    assert slot_path.is_file(), f"missing slot TOML: {slot_path}"
    cfg = _load_slot(slot_name)
    assert cfg.name == slot_name, f"{slot_name} name {cfg.name!r} != expected {slot_name!r}"
    assert cfg.port == port, f"{slot_name} port {cfg.port} != expected {port}"
    assert cfg.device == device, f"{slot_name} device {cfg.device} != expected {device}"
    assert cfg.profile == profile, f"{slot_name} profile {cfg.profile!r} != expected {profile!r}"


@pytest.mark.parametrize("slot_name,expected", list(EXPECTED_MAPPING.items()))
def test_static_seed_slot_populates_hw_grid(slot_name: str, expected: tuple) -> None:
    """Every static seed populates n_gpu_layers and threads (per spec §3.1 + §3.3)."""
    slot_path = SLOTS_DIR / f"{slot_name}.toml"
    if not slot_path.is_file():
        pytest.skip(f"slot TOML not yet created: {slot_name}")
    cfg = _load_slot(slot_name)
    # n_gpu_layers: -1 (all) for gpu-*, 0 for cpu, n/a for npu
    # Skip npu — FLM doesn't take -ngl; the field defaults to -1 (Schema default)
    if cfg.device.startswith("gpu-"):
        assert cfg.n_gpu_layers == -1, (
            f"{slot_name} device={cfg.device} but n_gpu_layers={cfg.n_gpu_layers} (expected -1)"
        )
    elif cfg.device == "cpu":
        assert cfg.n_gpu_layers == 0, (
            f"{slot_name} device=cpu but n_gpu_layers={cfg.n_gpu_layers} (expected 0)"
        )
    # threads: 0 for gpu-*/npu (let runtime pick), 8 for cpu
    if cfg.device == "cpu":
        assert cfg.threads == 8, (
            f"{slot_name} device=cpu but threads={cfg.threads} (expected 8)"
        )
    else:
        assert cfg.threads == 0, (
            f"{slot_name} device={cfg.device} but threads={cfg.threads} (expected 0)"
        )


def test_brain_slot_docstring_recommends_hal0_agent() -> None:
    """brain.toml docstring recommends tool_model = hal0/agent per spec-p3-brain §5a
    (legacy comment said hal0/code — that was the older recommendation)."""
    brain_path = SLOTS_DIR / "brain.toml"
    content = brain_path.read_text()
    assert "hal0/agent" in content, (
        "brain.toml docstring missing 'hal0/agent' recommendation per spec-p3-brain §5a"
    )
