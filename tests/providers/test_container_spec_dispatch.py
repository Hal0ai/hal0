"""load_sync routes slots to their spec provider (FLM/NPU, Kokoro/TTS)."""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import MagicMock, patch

from hal0.providers.container import ContainerProvider, _spec_provider_for


def _exec_start(unit_text: str) -> list[str]:
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line[len("ExecStart=") :])
    raise AssertionError("ExecStart not found")


_TEST_RUNTIME = "/usr/bin/docker"


# ── _spec_provider_for unit tests ─────────────────────────────────────────────


def test_spec_provider_npu_returns_flm() -> None:
    from hal0.providers.flm import FLMProvider

    result = _spec_provider_for({"device": "npu"})
    assert isinstance(result, FLMProvider)


def test_spec_provider_tts_type_returns_kokoro() -> None:
    from hal0.providers.kokoro import KokoroProvider

    result = _spec_provider_for({"device": "cpu", "type": "tts"})
    assert isinstance(result, KokoroProvider)


def test_spec_provider_kokoro_profile_returns_kokoro() -> None:
    from hal0.providers.kokoro import KokoroProvider

    result = _spec_provider_for({"device": "cpu", "profile": "kokoro-cpu"})
    assert isinstance(result, KokoroProvider)


def test_spec_provider_gpu_returns_none() -> None:
    result = _spec_provider_for({"device": "gpu-rocm", "profile": "moe-rocmfp4"})
    assert result is None


def test_spec_provider_vulkan_returns_none() -> None:
    result = _spec_provider_for({"device": "gpu-vulkan", "profile": "vulkan-server"})
    assert result is None


# ── load_sync kokoro TTS path ──────────────────────────────────────────────────


def test_tts_kokoro_slot_renders_spec_unit() -> None:
    """TTS/kokoro slot: spec unit rendered with --model_path, no --device=, correct --publish."""
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro-cpu",
        "model": {"default": "kokoro-v1"},
    }

    unit_captured: list[str] = []

    def fake_write_and_start(slot_name: str, unit_text: str) -> None:
        unit_captured.append(unit_text)

    with (
        patch("hal0.providers.container._container_runtime", return_value=_TEST_RUNTIME),
        patch.object(provider, "_write_and_start_unit", side_effect=fake_write_and_start),
    ):
        provider.load_sync(slot_cfg, {"_model_key": "kokoro-v1"})

    assert unit_captured, "load_sync never called _write_and_start_unit"
    argv = _exec_start(unit_captured[0])

    # Kokoro spec args present
    assert "--model_path" in argv
    # CPU: zero --device= flags
    assert not any(a.startswith("--device=") for a in argv)
    # Loopback publish present
    assert "--publish=127.0.0.1:8084:8084" in argv


def test_tts_slot_by_type_only_no_profile() -> None:
    """type=tts without explicit profile still routes through Kokoro."""
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        # no profile key — KokoroProvider falls back to _DEFAULT_PROFILE
    }

    unit_captured: list[str] = []

    def fake_write_and_start(slot_name: str, unit_text: str) -> None:
        unit_captured.append(unit_text)

    with (
        patch("hal0.providers.container._container_runtime", return_value=_TEST_RUNTIME),
        patch.object(provider, "_write_and_start_unit", side_effect=fake_write_and_start),
    ):
        provider.load_sync(slot_cfg, {})

    assert unit_captured, "load_sync never called _write_and_start_unit"
    argv = _exec_start(unit_captured[0])
    assert "--model_path" in argv
    assert not any(a.startswith("--device=") for a in argv)


def test_kokoro_path_does_not_require_registry_model_path() -> None:
    """kokoro-v1 is not a GGUF; model_info with NO 'path' must not raise.

    The llama path's _resolve_model_path raises ValueError on missing 'path';
    the kokoro spec path must never hit it.
    """
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro-cpu",
    }

    unit_captured: list[str] = []

    def fake_write_and_start(slot_name: str, unit_text: str) -> None:
        unit_captured.append(unit_text)

    with (
        patch("hal0.providers.container._container_runtime", return_value=_TEST_RUNTIME),
        patch.object(provider, "_write_and_start_unit", side_effect=fake_write_and_start),
    ):
        # model_info = {} — no 'path', must not raise
        provider.load_sync(slot_cfg, {})

    assert unit_captured


# ── GPU slot unaffected ────────────────────────────────────────────────────────


def test_gpu_slot_unaffected_still_takes_llama_path(tmp_path: Any) -> None:
    """device=gpu-rocm, profile=moe-rocmfp4 → llama _render_unit path.

    Mirrors TestLoadSyncNpuBranch.test_gpu_slot_unaffected_by_npu_branch style
    from test_container_npu.py: patches _resolve_profile + GPU helpers, then
    asserts --model present and /dev/accel absent.
    """
    from hal0.config.schema import ProfileConfig

    provider = ContainerProvider()
    profile = ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server",
        flags="-fa on",
        mtp=False,
    )
    unit_file = tmp_path / "hal0-slot@chat.service"

    def fake_run(*args: str, check: bool = True) -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        patch("hal0.providers.container._resolve_profile", return_value=profile),
        patch(
            "hal0.providers.container.resolve_gpu_device_paths",
            return_value=["/dev/kfd", "/dev/dri/renderD128"],
        ),
        patch(
            "hal0.providers.container.resolve_gpu_group_ids",
            return_value=[],
        ),
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=unit_file),
    ):
        provider.load_sync(
            {
                "name": "chat",
                "port": 8095,
                "profile": "moe-rocmfp4",
                "device": "gpu-rocm",
            },
            {"path": "/mnt/ai-models/model.gguf", "_model_key": "my-model"},
        )

    unit_text = unit_file.read_text()
    argv = _exec_start(unit_text)
    # llama-server path: --model present
    assert "--model" in argv
    assert "/mnt/ai-models/model.gguf" in argv
    # GPU device present, NPU device absent
    assert "--device=/dev/kfd" in argv
    assert "/dev/accel/accel0" not in unit_text
    # No --model_path (kokoro flag) in GPU unit
    assert "--model_path" not in unit_text


def test_npu_wins_over_tts_type() -> None:
    """device=npu is more specific than type=tts — FLM takes precedence."""
    from hal0.providers.container import _spec_provider_for
    from hal0.providers.flm import FLMProvider

    provider = _spec_provider_for({"device": "npu", "type": "tts"})
    assert isinstance(provider, FLMProvider)
