"""Model-owned `vision` tri-state gates the --mmproj emit (#901, spec-hw-slot-ownership §1).

The container provider emits --mmproj from the model sidecar (#900). The
MODEL carries an optional opt-out: ``defaults.vision = false`` boots any slot
bound to it text-only (no --mmproj -> modalities.vision:false), AUTO
(None/True) on where a sidecar exists so the chat slot gets vision for free.
The former PER-SLOT ``vision`` toggle is gone — ``SlotConfig`` carries no such
field anymore; a slot-config write carrying ``vision`` is hard-rejected at the
API boundary (see ``hal0.slot_config.MODEL_OWNED_SLOT_KEYS``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from hal0.config.schema import ProfileConfig
from hal0.providers.container import ContainerProvider

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


# ── container_spec gating ────────────────────────────────────────────────────


class TestVisionToggleGatesMmproj:
    def test_auto_none_emits_mmproj(self) -> None:
        """No explicit defaults.vision opinion + sidecar present → --mmproj emitted."""
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

    def test_slot_cfg_vision_key_is_ignored(self) -> None:
        """A stray legacy ``vision`` key still on a slot TOML (pre-migration,
        tolerated by SlotConfig's extra="allow") no longer has any effect —
        the model's defaults.vision is the only thing consulted."""
        spec = _build_spec(_slot_cfg(vision=False), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command
