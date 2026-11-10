"""An xdna slot launches the llama-server path, not FastFlowLM (ADR-0057).

A slot on the optional ``xdna`` runner sits on ``device = "npu"`` like the
FLM anchor, but it is a llama-server launch on the ggml-xdna image. The launch
dispatch used to send every ``device == "npu"`` slot to ``FLMProvider``, so an
xdna slot launched the FLM image and exited. These tests pin that the slot's
``binary`` decides: no spec provider (the llama path), the xdna runner's image,
and the xdna argv/devices/env on the rendered plan, while a plain NPU slot
still gets FLM.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from hal0.config.schema import ProfileConfig
from hal0.providers.container import (
    ContainerProvider,
    _resolve_image_ref,
    _spec_provider_for,
    runner_family_mismatch,
    slot_effective_context,
)
from hal0.providers.flm import FLMProvider
from hal0.runners import get_runner, resolve_runner_image


def _xdna_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "xdna",
        "port": 8131,
        "type": "llm",
        "device": "npu",
        "binary": "xdna",
        "runtime": "container",
        "model": {"default": "qwen3.5-0.8b-q4_k_m"},
    }
    base.update(overrides)
    return base


def _flm_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "flm",
        "port": 8082,
        "type": "llm",
        "device": "npu",
        "model": {"default": "gemma3:1b"},
    }
    base.update(overrides)
    return base


def _profile() -> Any:
    return SimpleNamespace(
        flags="",
        ctx_size=8192,
        vision="auto",
        speculation="auto",
        runner=None,
        runtime_family="llama-server",
    )


def test_xdna_slot_takes_the_llama_path() -> None:
    assert _spec_provider_for(_xdna_cfg()) is None


def test_plain_npu_slot_still_launches_flm() -> None:
    assert isinstance(_spec_provider_for(_flm_cfg()), FLMProvider)
    assert isinstance(_spec_provider_for(_flm_cfg(binary="flm")), FLMProvider)


def test_xdna_slot_resolves_the_xdna_runner_image() -> None:
    with patch("hal0.providers._image.operator_default_image", return_value=None):
        ref = _resolve_image_ref(_xdna_cfg(), None)
    assert ref == resolve_runner_image(get_runner("xdna"))
    assert "flm" not in ref


def test_flm_slot_image_is_unchanged() -> None:
    with patch("hal0.providers._image.operator_default_image", return_value=None):
        ref = _resolve_image_ref(_flm_cfg(), None)
    assert ref == FLMProvider().image_ref(_flm_cfg())


def test_xdna_slot_on_its_own_is_not_a_runner_mismatch() -> None:
    assert runner_family_mismatch(_xdna_cfg()) is None


def test_xdna_binary_under_an_flm_profile_is_a_mismatch() -> None:
    """An FLM profile would launch FastFlowLM in the xdna slot's place."""
    with patch("hal0.providers.container._profile_runtime_family", return_value="flm"):
        mismatch = runner_family_mismatch(_xdna_cfg(profile="flm"))
    assert mismatch is not None
    assert mismatch.details["binary"] == "xdna"
    assert mismatch.details["launches"] == "flm"


def test_xdna_slot_context_is_not_the_flm_fallback() -> None:
    profile = _profile()
    profile.ctx_size = None
    registry = SimpleNamespace(get=lambda _id: {"metadata": {"context_length": 16384}})
    with patch("hal0.providers.container._resolve_profile", return_value=profile):
        ctx, source = slot_effective_context(_xdna_cfg(), registry)
    # The llama path reads the model's native window; FLM's would report its
    # fixed 8192 fallback.
    assert (ctx, source) == (16384, "native")


def test_xdna_container_spec_carries_the_npu_launch() -> None:
    model_info = {"path": "/models/qwen3.5-0.8b.gguf", "_model_key": "qwen3.5-0.8b-q4_k_m"}
    cfg = _xdna_cfg(xdna={"env": {"GGML_XDNA_ARENA_MB": "512"}, "host_xrt": "/opt/xrt"})
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_profile()),
        patch("hal0.providers._image.operator_default_image", return_value=None),
        patch(
            "hal0.providers.container.resolve_npu_device_paths",
            return_value=["/dev/accel/accel0", "/dev/dri/renderD128"],
        ),
        patch("hal0.providers.container._dev_node_exists", return_value=True),
        patch("hal0.providers.container.resolve_npu_group_ids", return_value=["44", "993"]),
    ):
        plan = ContainerProvider().container_spec(cfg, model_info)

    assert plan.image == resolve_runner_image(get_runner("xdna"))
    tail = plan.command[plan.command.index("--model") :]
    for flag, value in (
        ("--device", "XDNA0"),
        ("-ngl", "99"),
        ("-fa", "on"),
        ("--poll", "0"),
        ("-np", "1"),
        ("--cache-reuse", "256"),
    ):
        assert tail[tail.index(flag) + 1] == value, flag
    assert "/dev/accel/accel0" in plan.devices
    assert "/dev/kfd" not in plan.devices
    assert plan.group_add == ["44", "993"]
    assert "memlock=-1" in plan.ulimits
    assert plan.env["GGML_XDNA_ARENA_MB"] == "512"
    assert plan.env["HAL0_XDNA_HOST_XRT"] == "/opt/xrt"
    assert any(str(getattr(m, "source", "")) == "/opt/xrt" for m in plan.mounts)
    assert plan.env["GGML_XDNA_PGEMM_GLU_A"] == "0"


def test_launch_forces_glu_a_off_over_a_hand_edited_toml() -> None:
    """A TOML the write seam never saw cannot flip GLU_A back on (ADR-0057 d3)."""
    model_info = {"path": "/models/qwen3.5-0.8b.gguf", "_model_key": "qwen3.5-0.8b-q4_k_m"}
    cfg = _xdna_cfg(
        xdna={"env": {"GGML_XDNA_PGEMM_GLU_A": "1"}},
        server={"env": {"GGML_XDNA_PGEMM_GLU_A": "1"}},
    )
    with (
        patch("hal0.providers.container._resolve_profile", return_value=_profile()),
        patch("hal0.providers._image.operator_default_image", return_value=None),
        patch("hal0.providers.container.resolve_npu_device_paths", return_value=[]),
        patch("hal0.providers.container.resolve_npu_group_ids", return_value=[]),
    ):
        plan = ContainerProvider().container_spec(cfg, model_info)
    assert plan.env["GGML_XDNA_PGEMM_GLU_A"] == "0"


def _xdna_detail(cfg: dict[str, Any]) -> dict[str, Any]:
    from hal0.providers.container import resolved_argv_detail_for_slot

    model_info = {"path": "/models/qwen3.5-0.8b.gguf", "_model_key": "qwen3.5-0.8b-q4_k_m"}
    with (
        patch("hal0.providers.container._resolve_profile", return_value=ProfileConfig()),
        patch("hal0.providers.container._best_effort_model_info", return_value=model_info),
        patch("hal0.providers._image.operator_default_image", return_value=None),
    ):
        return resolved_argv_detail_for_slot(cfg)


def test_xdna_slot_tune_reaches_the_launch() -> None:
    """A slot [tune] window lands on an xdna slot's argv like a GPU slot's (#3536)."""
    d = _xdna_detail(_xdna_cfg(tune={"ctx_size": 4096}))
    argv = d["argv"]
    assert argv[argv.index("--ctx-size") + 1] == "4096"
    assert d["tune_effective"]["sources"]["ctx_size"] == "slot"
    assert set(d["tune_effective"]["tune_unavailable"]) == {"speculation"}


def test_gpu_slot_has_no_tune_unavailable() -> None:
    d = _xdna_detail(_xdna_cfg(device="gpu-rocm", binary="rocm"))
    assert d["tune_effective"]["tune_unavailable"] == {}
