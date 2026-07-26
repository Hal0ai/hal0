"""MODEL-owned `vision` toggle gates the --mmproj emit (#901).

The container provider emits --mmproj from the model sidecar (#900). Vision
is a per-model opt-out now (spec-hw-slot-ownership §1 — replaces the former
per-slot `SlotConfig.vision` dual-writer): `ModelDefaults.vision = false`
boots every slot bound to that model text-only (no --mmproj →
modalities.vision:false), default-on where a sidecar exists so a chat model
gets vision for free.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from hal0.config.schema import ProfileConfig
from hal0.providers.container import ContainerProvider
from hal0.registry.model import ModelDefaults

_SIDECAR = "/mnt/ai-models/qwopus3.6-27b-v2/mmproj-F32.mmproj"


def _moe_profile() -> ProfileConfig:
    return ProfileConfig(
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


def _model_info(*, vision: bool | None = None, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"path": "/mnt/ai-models/qwopus/qwopus.gguf", "_model_key": "chat-vlm"}
    if vision is not None:
        base["defaults"] = {"vision": vision}
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


# ── schema: the flag lives on ModelDefaults now, tri-state, default-on ──────


class TestModelDefaultsVisionField:
    def test_vision_defaults_none(self) -> None:
        defaults = ModelDefaults()
        assert defaults.vision is None

    def test_vision_opt_out_round_trips(self) -> None:
        defaults = ModelDefaults(vision=False)
        assert defaults.vision is False
        assert defaults.model_dump()["vision"] is False


# ── container_spec gating ────────────────────────────────────────────────────


class TestVisionToggleGatesMmproj:
    def test_default_on_emits_mmproj(self) -> None:
        """No explicit vision opinion + sidecar present → --mmproj emitted."""
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command
        assert spec.command[spec.command.index("--mmproj") + 1] == _SIDECAR

    def test_vision_true_emits_mmproj(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info(vision=True, mmproj=_SIDECAR))
        assert "--mmproj" in spec.command

    def test_vision_false_suppresses_mmproj(self) -> None:
        """defaults.vision=false → text-only, no --mmproj even though a sidecar exists."""
        spec = _build_spec(_slot_cfg(), _model_info(vision=False, mmproj=_SIDECAR))
        assert "--mmproj" not in spec.command, (
            f"defaults.vision=false must suppress --mmproj: {spec.command}"
        )

    def test_no_sidecar_no_mmproj_regardless(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info(vision=True))  # no sidecar
        assert "--mmproj" not in spec.command

    def test_slot_config_can_no_longer_carry_vision(self) -> None:
        """A stray slot-side `vision` key has NO effect on the launch decision —
        only ModelDefaults.vision is read now (spec-hw-slot-ownership §1). A
        pre-migration slot TOML may still have the key (round-trips harmlessly
        via SlotConfig's extra="allow"), but it must not gate --mmproj."""
        spec = _build_spec(_slot_cfg(vision=False), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command, (
            "a slot-side vision=false must be ignored; only the model decides"
        )
