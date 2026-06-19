"""Per-slot `vision` toggle gates the --mmproj emit (#901).

The container provider emits --mmproj from the model sidecar (#900). This
adds a per-slot opt-out: `vision = false` boots the slot text-only (no
--mmproj → modalities.vision:false), default-on where a sidecar exists so
the chat slot gets vision for free.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from hal0.config.schema import ProfileConfig, SlotConfig
from hal0.providers.container import ContainerProvider

_SIDECAR = "/mnt/ai-models/qwopus3.6-27b-v2/mmproj-F32.mmproj"


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        flags="-fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap",
        mtp=False,
    )


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "chat",
        "port": 8102,
        "profile": "rocm",
        "runtime": "container",
        "device": "gpu-rocm",
        "model": {"default": "chat-vlm"},
    }
    base.update(overrides)
    return base


def _model_info(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"path": "/mnt/ai-models/qwopus/qwopus.gguf", "_model_key": "chat-vlm"}
    base.update(overrides)
    return base


def _build_spec(slot_cfg: dict[str, Any], model_info: dict[str, Any]):
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_moe_profile()),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        return provider.container_spec(slot_cfg, model_info)


# ── schema: the flag exists, default-on ──────────────────────────────────────


class TestSlotConfigVisionField:
    def test_vision_defaults_on(self) -> None:
        cfg = SlotConfig(name="chat", port=8102, model={"default": "chat-vlm"})
        assert cfg.vision is True

    def test_vision_opt_out_round_trips(self) -> None:
        cfg = SlotConfig(name="chat", port=8102, model={"default": "chat-vlm"}, vision=False)
        assert cfg.vision is False
        assert cfg.model_dump()["vision"] is False


# ── container_spec gating ────────────────────────────────────────────────────


class TestVisionToggleGatesMmproj:
    def test_default_on_emits_mmproj(self) -> None:
        """No explicit vision flag + sidecar present → --mmproj emitted."""
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command
        assert spec.command[spec.command.index("--mmproj") + 1] == _SIDECAR

    def test_vision_true_emits_mmproj(self) -> None:
        spec = _build_spec(_slot_cfg(vision=True), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command

    def test_vision_false_suppresses_mmproj(self) -> None:
        """vision=false → text-only, no --mmproj even though a sidecar exists."""
        spec = _build_spec(_slot_cfg(vision=False), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" not in spec.command, (
            f"vision=false must suppress --mmproj: {spec.command}"
        )

    def test_no_sidecar_no_mmproj_regardless(self) -> None:
        spec = _build_spec(_slot_cfg(vision=True), _model_info())  # no sidecar
        assert "--mmproj" not in spec.command
