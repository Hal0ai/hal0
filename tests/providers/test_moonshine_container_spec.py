"""Moonshine STT container spec (CPU-only, mirrors the kokoro spec contract)."""

from typing import Any

import pytest

from hal0.providers.container import _render_quadlet_from_plan
from hal0.providers.moonshine import (
    MoonshineProvider,
    MoonshineWeightsMissingError,
    check_moonshine_weights,
)


def _render_from_spec(token, spec, *, publish_host="127.0.0.1"):
    return _render_quadlet_from_plan(token, spec, publish_host=publish_host)


def _exec(unit):
    for line in unit.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    raise AssertionError("Exec not found")


@pytest.fixture(autouse=True)
def _pin_model_store(monkeypatch) -> None:
    """Pin the store resolver to the literal /mnt/ai-models mount source
    (same rationale as the kokoro spec module)."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


@pytest.fixture()
def bundle(tmp_path) -> Any:
    """A staged-looking moonshine bundle: root/quantized/<variant>/ with weights."""
    leaf = tmp_path / "moonshine" / "quantized" / "small-streaming-en"
    leaf.mkdir(parents=True)
    (leaf / "encoder_model.ort").write_bytes(b"\0")
    (leaf / "decoder_model_merged.ort").write_bytes(b"\0")
    return tmp_path / "moonshine"


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "stt",
        "port": 8089,
        "device": "cpu",
        "type": "transcription",
        "runtime": "container",
        "profile": "moonshine",
    }
    base.update(overrides)
    return base


def _model_info(bundle) -> dict[str, Any]:
    return {
        "path": str(bundle),
        "metadata": {"variant": "small-streaming-en"},
    }


def test_spec_has_no_gpu_devices_or_groups(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(), _model_info(bundle))
    assert spec.devices == []
    assert spec.group_add == []


def test_spec_command_carries_port_host_model_path_and_arch(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(), _model_info(bundle))
    c = spec.command
    assert c[c.index("--port") + 1] == "8089"
    assert c[c.index("--host") + 1] == "0.0.0.0"
    # Registry path beats the profile-baked default AND resolves the
    # quantized/<variant> leaf so the in-container loader can't fall back
    # to a network download.
    assert c[c.index("--model_path") + 1] == str(bundle / "quantized" / "small-streaming-en")
    assert c[c.index("--model_arch") + 1] == "small_streaming"


def test_spec_mounts_model_store_ro_and_publishes_loopback(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(), _model_info(bundle))
    ai_mount = next(m for m in spec.mounts if m.source == "/mnt/ai-models")
    assert ai_mount.read_only is True
    assert ai_mount.target == "/mnt/ai-models"
    assert spec.port == 8089
    assert spec.network_mode == ""


def test_spec_security_opts_for_lxc(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(), _model_info(bundle))
    assert "apparmor=unconfined" in spec.security_opt
    assert "seccomp=unconfined" in spec.security_opt


def test_renderer_no_device_args_publish_volume_command(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(), _model_info(bundle))
    unit = _render_from_spec("stt", spec)
    lines = unit.splitlines()

    assert not any(line.startswith("AddDevice=") for line in lines), (
        "CPU slot must not pass any device nodes"
    )
    assert "PublishPort=127.0.0.1:8089:8089" in lines
    assert "Volume=/mnt/ai-models:/mnt/ai-models:ro" in unit
    exec_argv = _exec(unit)
    assert "--model_path" in exec_argv
    assert "--model_arch" in exec_argv
    assert "8089" in exec_argv


def test_slot_port_override_wins(bundle) -> None:
    spec = MoonshineProvider().container_spec(_slot_cfg(port=8098), _model_info(bundle))
    c = spec.command
    assert c[c.index("--port") + 1] == "8098"
    assert spec.port == 8098

    unit = _render_from_spec("stt", spec)
    assert "PublishPort=127.0.0.1:8098:8098" in unit.splitlines()


# ── Weight preflight (spawn fails by artifact name, not first-request 500) ─────


def test_spec_preflight_names_missing_bundle(tmp_path) -> None:
    missing = tmp_path / "nope" / "moonshine"
    with pytest.raises(MoonshineWeightsMissingError) as exc_info:
        MoonshineProvider().container_spec(_slot_cfg(), {"path": str(missing)})
    assert str(missing) in str(exc_info.value)
    assert exc_info.value.code == "slot.weights_missing"


def test_preflight_rejects_dir_without_weights(tmp_path) -> None:
    empty = tmp_path / "moonshine"
    empty.mkdir()
    with pytest.raises(MoonshineWeightsMissingError) as exc_info:
        check_moonshine_weights(str(empty))
    assert str(empty) in str(exc_info.value)


def test_preflight_rejects_streaming_only_bundle(tmp_path) -> None:
    """Live gotcha (lxc105): the streaming-SDK file set (encoder.ort /
    frontend.ort) is NOT loadable by this image — the server resolves
    encoder_model.{ort,onnx}. Accepting it produced a container that started
    and then failed every transcription."""
    streaming = tmp_path / "moonshine" / "quantized"
    streaming.mkdir(parents=True)
    for name in ("encoder.ort", "frontend.ort", "cross_kv.ort", "tokenizer.bin"):
        (streaming / name).write_bytes(b"\0")
    with pytest.raises(MoonshineWeightsMissingError) as exc_info:
        check_moonshine_weights(str(tmp_path / "moonshine"))
    assert "streaming" in str(exc_info.value).lower()


def test_leaf_resolves_bundle_nested_without_variant_dir(tmp_path) -> None:
    """The staged tree also uses <root>/quantized/ directly (no <variant>
    subdir) — the resolver must find the encoder there too."""
    from hal0.providers.moonshine import _resolve_model_leaf

    leaf = tmp_path / "base-en" / "quantized"
    leaf.mkdir(parents=True)
    (leaf / "encoder_model.ort").write_bytes(b"\0")
    resolved = _resolve_model_leaf(str(tmp_path / "base-en"), "base-en")
    assert resolved == str(leaf)


def test_preflight_allows_empty_path_hf_fallback() -> None:
    """No --model_path → the in-container HuggingFace fallback is legal."""
    check_moonshine_weights("")  # must not raise


def test_preflight_accepts_staged_bundle(bundle) -> None:
    check_moonshine_weights(str(bundle))  # must not raise
