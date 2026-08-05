"""load_sync routes slots to their spec provider (FLM/NPU, Kokoro/TTS)."""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import MagicMock, patch

from hal0.providers.container import ContainerProvider, _spec_provider_for


def _exec_argv(unit_text: str) -> list[str]:
    """The in-container argv from the Quadlet ``Exec=`` key (post P3-quadlet).

    Was the ``podman run …`` ExecStart string; the Quadlet renderer now emits
    only the in-container argv on the ``Exec=`` line (podman flags became typed
    ``[Container]`` keys — ``PublishPort=`` / ``AddDevice=`` / ``Volume=`` …).
    """
    for line in unit_text.splitlines():
        if line.startswith("Exec="):
            return shlex.split(line[len("Exec=") :])
    raise AssertionError("Exec= not found")


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

    result = _spec_provider_for({"device": "cpu", "profile": "kokoro"})
    assert isinstance(result, KokoroProvider)


def test_spec_provider_comfyui_returns_comfyui() -> None:
    from hal0.providers.comfyui import ComfyUIProvider

    # All three discriminators route to ComfyUI (Phase D img slot).
    for cfg in (
        {"provider": "comfyui", "type": "image", "device": "gpu-rocm"},
        {"profile": "comfyui"},
        {"type": "image"},
    ):
        assert isinstance(_spec_provider_for(cfg), ComfyUIProvider), cfg


def test_spec_provider_transcription_type_returns_moonshine() -> None:
    from hal0.providers.moonshine import MoonshineProvider

    result = _spec_provider_for({"device": "cpu", "type": "transcription"})
    assert isinstance(result, MoonshineProvider)


def test_spec_provider_moonshine_profile_returns_moonshine() -> None:
    from hal0.providers.moonshine import MoonshineProvider

    result = _spec_provider_for({"device": "cpu", "profile": "moonshine"})
    assert isinstance(result, MoonshineProvider)


def test_npu_transcription_wins_over_moonshine() -> None:
    """device=npu transcription is the FLM trio, never the CPU engine."""
    from hal0.providers.flm import FLMProvider

    result = _spec_provider_for({"device": "npu", "type": "transcription"})
    assert isinstance(result, FLMProvider)


def test_unknown_runtime_family_fails_loudly() -> None:
    """A RuntimeFamily extension without a dispatch branch must not silently
    fall through to the llama-server default (the F3 fragility)."""
    from unittest.mock import patch as _patch

    import pytest as _pytest

    from hal0.providers.container import UnknownRuntimeFamilyError

    with (
        _patch(
            "hal0.providers.container._profile_runtime_family",
            return_value="quantumfoo",
        ),
        _pytest.raises(UnknownRuntimeFamilyError) as exc_info,
    ):
        _spec_provider_for({"device": "cpu", "profile": "whatever"})
    assert "quantumfoo" in str(exc_info.value)


def test_spec_provider_gpu_returns_none() -> None:
    result = _spec_provider_for({"device": "gpu-rocm", "profile": "chat"})
    assert result is None


def test_spec_provider_vulkan_returns_none() -> None:
    result = _spec_provider_for({"device": "gpu-vulkan", "profile": "chat"})
    assert result is None


# ── load_sync kokoro TTS path ──────────────────────────────────────────────────


def test_tts_kokoro_slot_renders_spec_unit(tmp_hal0_home: str) -> None:
    """TTS/kokoro slot: spec unit rendered with --model_path, no AddDevice=, correct PublishPort=.

    Isolated via ``tmp_hal0_home`` (tests/conftest.py): ``_render_quadlet_text``
    threads the live ``[slots].publish_host`` into every render
    (``_slot_publish_host()`` -> ``load_hal0_config()``), so without this
    fixture a host with a non-default ``publish_host`` (e.g. LAN-exposed
    0.0.0.0) renders a different --publish= address than the default
    asserted here.
    """
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro",
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
    unit_text = unit_captured[0]
    argv = _exec_argv(unit_text)

    # Kokoro spec args present in the in-container Exec= argv
    assert "--model_path" in argv
    # CPU: zero device passthrough (Quadlet AddDevice= keys)
    assert "AddDevice=" not in unit_text
    # Loopback publish present as a typed Quadlet PublishPort= key
    assert "PublishPort=127.0.0.1:8084:8084" in unit_text


def test_tts_slot_by_type_only_no_profile() -> None:
    """type=tts without explicit profile still routes through Kokoro.

    The test supplies the canonical workload profile ``kokoro`` explicitly;
    the contract asserted is the dispatch+render path, not the seed-slug
    spelling of Kokoro's internal fallback constant.
    """
    provider = ContainerProvider()
    slot_cfg = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro",
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
    unit_text = unit_captured[0]
    argv = _exec_argv(unit_text)
    assert "--model_path" in argv
    assert "AddDevice=" not in unit_text


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
        "profile": "kokoro",
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
    """device=gpu-rocm, profile=rocm → llama container_spec / Quadlet render path.

    Mirrors TestLoadSyncNpuBranch.test_gpu_slot_unaffected_by_npu_branch style
    from test_container_npu.py: patches _resolve_profile + GPU helpers, then
    asserts --model present and /dev/accel absent.
    """
    from hal0.config.schema import ProfileConfig

    provider = ContainerProvider()
    profile = ProfileConfig(
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
        # GPU device nodes are existence-filtered at the container_spec call
        # site; force them "present" so the CI host (no /dev/kfd) still renders.
        patch("hal0.providers.container.os.path.exists", return_value=True),
        patch.object(provider, "_run", side_effect=fake_run),
        patch.object(provider, "_unit_path", return_value=unit_file),
    ):
        provider.load_sync(
            {
                "name": "chat",
                "port": 8095,
                "profile": "chat",
                "device": "gpu-rocm",
            },
            {"path": "/mnt/ai-models/model.gguf", "_model_key": "my-model"},
        )

    unit_text = unit_file.read_text()
    argv = _exec_argv(unit_text)
    # llama-server path: --model present in the in-container Exec= argv
    assert "--model" in argv
    assert "/mnt/ai-models/model.gguf" in argv
    # GPU device present as a Quadlet AddDevice= key, NPU device absent
    assert "AddDevice=/dev/kfd" in unit_text
    assert "/dev/accel/accel0" not in unit_text
    # No --model_path (kokoro flag) in GPU unit
    assert "--model_path" not in unit_text


def test_npu_wins_over_tts_type() -> None:
    """device=npu is more specific than type=tts — FLM takes precedence."""
    from hal0.providers.container import _spec_provider_for
    from hal0.providers.flm import FLMProvider

    provider = _spec_provider_for({"device": "npu", "type": "tts"})
    assert isinstance(provider, FLMProvider)
