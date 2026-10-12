"""The profile's ``vision`` (``auto`` / ``off``) gates the --mmproj emit (ADR-0029).

The container provider emits --mmproj from the model sidecar (#900): the
projector path is a fact about the model file. Whether it loads is the
profile's ``vision``: ``auto`` loads it when the model has one, ``off`` boots
the slot text-only (no --mmproj -> modalities.vision:false). A ``vision`` key
left on the model row or on a slot TOML is not a switch any more: the model
row's is ignored, and a slot carrying one is unconverted and refuses to launch.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import UnprocessableEntity
from hal0.providers.container import ContainerProvider

_SIDECAR = "/mnt/ai-models/qwopus3.6-27b-v2/mmproj-F32.mmproj"


def _profile(vision: str = "auto") -> ProfileConfig:
    return ProfileConfig(flags="-fa on -b 512 -ub 512 --parallel 1 --no-mmap", vision=vision)


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


def _build_spec(
    slot_cfg: dict[str, Any], model_info: dict[str, Any], profile: ProfileConfig | None = None
):
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container._resolve_profile", return_value=profile or _profile()),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container.resolve_gpu_group_ids", return_value=[]),
    ):
        return provider.container_spec(slot_cfg, model_info)


# ── container_spec gating ────────────────────────────────────────────────────


class TestVisionToggleGatesMmproj:
    def test_auto_emits_mmproj(self) -> None:
        """Profile ``vision = "auto"`` + sidecar present → --mmproj emitted."""
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR))
        assert "--mmproj" in spec.command
        assert spec.command[spec.command.index("--mmproj") + 1] == _SIDECAR

    def test_off_suppresses_mmproj(self) -> None:
        """Profile ``vision = "off"`` → text-only, no --mmproj even though a
        sidecar exists, and no ``--no-mmproj`` either (nothing to cancel)."""
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR), _profile("off"))
        assert "--mmproj" not in spec.command, spec.command
        assert "--no-mmproj" not in spec.command

    def test_no_sidecar_no_mmproj_regardless(self) -> None:
        spec = _build_spec(_slot_cfg(), _model_info())  # no sidecar
        assert "--mmproj" not in spec.command

    def test_model_row_vision_key_is_not_a_switch(self) -> None:
        """A ``vision`` left on the model row is not consulted: the profile's
        ``auto`` still loads the projector."""
        spec = _build_spec(_slot_cfg(), _model_info(mmproj=_SIDECAR, defaults={"vision": False}))
        assert "--mmproj" in spec.command

    def test_slot_vision_key_refuses_to_launch(self) -> None:
        """A stray ``vision`` key on a slot TOML is pre-conversion debris: the
        slot is unconverted and does not launch."""
        with pytest.raises(UnprocessableEntity) as excinfo:
            _build_spec(_slot_cfg(vision=False), _model_info(mmproj=_SIDECAR))
        assert excinfo.value.code == "slot.unconverted"
