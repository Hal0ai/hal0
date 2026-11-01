"""load_sync routes slots to their spec provider (FLM/NPU, Kokoro/TTS)."""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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


def test_spec_provider_halogen_profile_returns_halogen(tmp_hal0_home: str) -> None:
    from hal0.providers.halogen import HalogenProvider

    # The seeded ``halogen`` profile carries ``runner = "halogen"``; the slot's
    # type and device must not shadow the family (a halogen slot is ``llm``).
    result = _spec_provider_for(
        {"device": "gpu-rocm", "type": "llm", "profile": "halogen", "name": "flash"}
    )
    assert isinstance(result, HalogenProvider)


def test_provider_for_family_halogen_does_not_resolve_a_profile() -> None:
    from hal0.providers.container import _provider_for_family
    from hal0.providers.halogen import HalogenProvider

    result = _provider_for_family({"device": "gpu-rocm", "type": "llm"}, "halogen")
    assert isinstance(result, HalogenProvider)


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


@pytest.mark.usefixtures("kokoro_store")
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


@pytest.mark.usefixtures("kokoro_store")
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


@pytest.mark.usefixtures("kokoro_store")
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
        speculation="off",
    )
    unit_file = tmp_path / "hal0-slot@chat.service"

    def fake_run(*args: str, check: bool = True, timeout: float | None = None) -> MagicMock:
        m = MagicMock()
        m.returncode = 0
        return m

    with (
        patch("hal0.providers.container._resolve_profile", return_value=profile),
        # #1888 guard: this test exercises UNIT RENDERING, not host GPU
        # validity — the box running the suite has no /dev/kfd.
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
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
        patch("hal0.providers.container._dev_node_exists", return_value=True),
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


# ── model ↔ runtime agreement at load ─────────────────────────────────────────

_HALOGEN_MODEL_INFO = {"_model_key": "flash-next", "backends": ["halogen"], "path": "/m/f.hgn"}
_GGUF_MODEL_INFO = {"_model_key": "qwen3-4b", "backends": ["rocm"], "path": "/m/q.gguf"}


def _agreement_probe() -> tuple[ContainerProvider, MagicMock]:
    """A provider whose every unit write is observable through one fake seam."""
    seam = MagicMock()
    provider = ContainerProvider()
    return provider, seam


def test_load_sync_refuses_mismatch_before_writing_unit() -> None:
    """A halogen checkpoint on a llama slot is refused before ANY unit is written."""
    from hal0.slots.state import SlotModelRuntimeMismatch

    provider, seam = _agreement_probe()
    slot_cfg = {"name": "agent", "port": 8095, "profile": "chat", "device": "gpu-rocm"}

    with (
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container._SYSTEMCTL_SEAM", seam),
        patch.object(provider, "_write_and_start_unit") as write_and_start,
        patch.object(provider, "_render_quadlet_text") as render,
        pytest.raises(SlotModelRuntimeMismatch) as exc_info,
    ):
        provider.load_sync(slot_cfg, _HALOGEN_MODEL_INFO)

    err = exc_info.value
    assert err.code == "slot.model_runtime_mismatch"
    assert err.status == 400
    assert "'flash-next'" in str(err)
    assert "'agent'" in str(err)
    assert "llama-server runtime" in str(err)
    seam.write_quadlet.assert_not_called()
    write_and_start.assert_not_called()
    render.assert_not_called()


def test_load_sync_refuses_gguf_on_halogen_slot_before_writing_unit() -> None:
    """The inverse: a halogen-runtime slot handed a GGUF never reaches a unit write."""
    from types import SimpleNamespace

    from hal0.slots.state import SlotModelRuntimeMismatch

    provider, seam = _agreement_probe()
    slot_cfg = {"name": "flash", "port": 8096, "profile": "hgn", "device": "gpu-rocm"}

    with (
        patch(
            "hal0.providers.container._spec_provider_for",
            return_value=SimpleNamespace(name="halogen"),
        ),
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch("hal0.providers.container._SYSTEMCTL_SEAM", seam),
        patch.object(provider, "_write_and_start_unit") as write_and_start,
        pytest.raises(SlotModelRuntimeMismatch) as exc_info,
    ):
        provider.load_sync(slot_cfg, _GGUF_MODEL_INFO)

    assert exc_info.value.code == "slot.model_runtime_mismatch"
    assert "not a halogen checkpoint" in str(exc_info.value)
    seam.write_quadlet.assert_not_called()
    write_and_start.assert_not_called()


def test_load_sync_empty_model_binding_is_not_this_guards_refusal() -> None:
    """A halogen slot with no model must not trip the guard (the provider names
    the missing model later, ``slot.model_missing``)."""
    from types import SimpleNamespace

    provider, _seam = _agreement_probe()
    slot_cfg = {"name": "flash", "port": 8096, "profile": "hgn", "device": "gpu-rocm"}

    with (
        patch(
            "hal0.providers.container._spec_provider_for",
            return_value=SimpleNamespace(name="halogen"),
        ),
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch.object(provider, "_render_quadlet_text", return_value="[Container]\n") as render,
        patch.object(provider, "_write_and_start_unit") as write_and_start,
    ):
        provider.load_sync(slot_cfg, {})

    render.assert_called_once()
    write_and_start.assert_called_once()
