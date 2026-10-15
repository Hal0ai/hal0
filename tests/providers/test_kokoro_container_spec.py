"""Kokoro TTS container spec (Phase B; store-pulled bundle, #2739)."""

from pathlib import Path
from typing import Any

import pytest

from hal0.providers.container import _render_quadlet_from_plan
from hal0.providers.kokoro import KokoroProvider, KokoroWeightsMissingError


def _render_from_spec(token, spec, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Quadlet renderer shim (ignores the legacy runtime_bin kwarg)."""
    return _render_quadlet_from_plan(token, spec, publish_host=publish_host)


def _exec(unit):
    for line in unit.splitlines():
        if line.startswith("Exec="):
            return line[len("Exec=") :]
    raise AssertionError("Exec not found")


@pytest.fixture(autouse=True)
def store(kokoro_store: Path) -> str:
    """Every spec in this module renders against a store holding the bundle."""
    return str(kokoro_store)


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "kokoro",
        "model": {"default": "kokoro-v1"},
    }
    base.update(overrides)
    return base


def test_spec_has_no_gpu_devices_or_groups() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert spec.devices == []
    assert spec.group_add == []


def test_spec_command_carries_port_host_and_model_path(store: str) -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8084"
    assert c[c.index("--host") + 1] == "0.0.0.0"
    # Store-derived, not a profile-baked operator path (#2739).
    assert c[c.index("--model_path") + 1] == f"{store}/tts/kokoro-v1"
    assert c.count("--model_path") == 1


def test_model_path_follows_a_non_default_store(tmp_path, monkeypatch) -> None:
    """The flag moves with ``[models].store``: the old follow-up note is gone."""
    from tests.providers.conftest import stage_kokoro_bundle

    other = tmp_path / "elsewhere"
    stage_kokoro_bundle(other)
    monkeypatch.setenv("HAL0_MODEL_STORE", str(other))
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    c = spec.command
    assert c[c.index("--model_path") + 1] == f"{other}/tts/kokoro-v1"
    assert any(m.source == str(other) and m.read_only for m in spec.mounts)


def test_registry_bound_bundle_wins(tmp_path) -> None:
    """A slot bound to a pulled row launches that row's directory."""
    bundle = tmp_path / "custom"
    bundle.mkdir()
    (bundle / "kokoro-v1.0.onnx").write_bytes(b"x")
    (bundle / "voices-v1.0.bin").write_bytes(b"x")
    spec = KokoroProvider().container_spec(_slot_cfg(), {"path": str(bundle / "kokoro-v1.0.onnx")})
    c = spec.command
    assert c[c.index("--model_path") + 1] == str(bundle)


def test_profile_model_path_is_dropped(store: str, monkeypatch) -> None:
    """A path in profile flags never reaches the runner: the store bundle wins."""
    from types import SimpleNamespace

    import hal0.providers.container as container_mod

    legacy = "--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx --default_voice af_sky"
    monkeypatch.setattr(
        container_mod,
        "resolve_named_profile",
        lambda _cfg, _name: SimpleNamespace(resolved_flags=legacy),
    )
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert spec.command[spec.command.index("--default_voice") + 1] == "af_sky"
    c = spec.command
    assert "/mnt/ai-models/local/kokoro-v1/kokoro-onnx" not in c
    assert c[c.index("--model_path") + 1] == f"{store}/tts/kokoro-v1"


def test_missing_bundle_fails_typed_and_names_the_pull(tmp_path, monkeypatch) -> None:
    """No bundle in the store: a typed error, never a silent GitHub download."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path / "empty"))
    with pytest.raises(KokoroWeightsMissingError) as exc_info:
        KokoroProvider().container_spec(_slot_cfg(), {})
    assert exc_info.value.code == "slot.weights_missing"
    assert "hal0 model pull kokoro-v1" in exc_info.value.message


def test_pre_fix_mislabeled_pull_fails_typed(tmp_path, monkeypatch) -> None:
    """A pre-#2739 pull (model.gguf, no voices pack) is not a loadable bundle."""
    store = tmp_path / "old"
    bundle = store / "tts" / "kokoro-v1"
    bundle.mkdir(parents=True)
    (bundle / "model.gguf").write_bytes(b"onnx bytes, wrong name")
    monkeypatch.setenv("HAL0_MODEL_STORE", str(store))
    with pytest.raises(KokoroWeightsMissingError) as exc_info:
        KokoroProvider().container_spec(_slot_cfg(), {"path": str(bundle / "model.gguf")})
    assert set(exc_info.value.details["missing"]) == {"kokoro-*.onnx", "voices*.bin"}
    assert "hal0 model pull kokoro-v1" in exc_info.value.message


def test_spec_mounts_model_store_and_publishes_loopback(store: str) -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert any(m.source == store for m in spec.mounts)
    assert spec.port == 8084
    assert spec.network_mode == ""


def test_spec_security_opts_for_lxc() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert "apparmor=unconfined" in spec.security_opt
    assert "seccomp=unconfined" in spec.security_opt


def test_spec_ro_mount_is_read_only(store: str) -> None:
    """Model-store mount must be read-only via the first-class Mount flag so
    the renderer emits a :ro --volume (no :ro target-string smuggling)."""
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    ai_mount = next(m for m in spec.mounts if m.source == store)
    assert ai_mount.read_only is True
    assert ai_mount.target == store
    assert ai_mount.selinux == "z"  # SELinux relabel for enforcing hosts (Fedora)
    assert ai_mount.render_quadlet() == f"{store}:{store}:ro,z"


# ── Renderer integration test ──────────────────────────────────────────────────


def test_renderer_no_device_args_publish_volume_command(store: str) -> None:
    """The Quadlet renderer renders the kokoro spec correctly.

    Checks:
    - No AddDevice= keys (CPU-only slot).
    - PublishPort=127.0.0.1:8084:8084 present.
    - Volume key with the model store present.
    - Exec argv contains --model_path and --port.
    """
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    unit = _render_from_spec("tts", spec, runtime_bin="/usr/bin/docker")
    lines = unit.splitlines()

    assert not any(line.startswith("AddDevice=") for line in lines), (
        "CPU slot must not pass any device nodes"
    )
    assert "PublishPort=127.0.0.1:8084:8084" in lines
    # Pins the 3-colon podman ro syntax; a substring check would also pass on a
    # malformed 4-colon render (the SELinux :z suffix is appended by the mount).
    assert f"Volume={store}:{store}:ro" in unit
    exec_argv = _exec(unit)
    assert "--model_path" in exec_argv
    assert "--port" in exec_argv
    assert "8084" in exec_argv


def test_slot_port_override_wins() -> None:
    """Slot port overrides the 8084 default end-to-end (spec + render)."""
    spec = KokoroProvider().container_spec(_slot_cfg(port=8097), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8097"
    assert spec.port == 8097

    unit = _render_from_spec("tts", spec, runtime_bin="/usr/bin/docker")
    assert "PublishPort=127.0.0.1:8097:8097" in unit.splitlines()


def test_named_missing_profile_refuses_to_launch() -> None:
    """A slot naming a profile the catalog lacks fails closed (ADR-0029); it
    never launches some other tune."""
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity) as exc_info:
        KokoroProvider().container_spec(_slot_cfg(profile="no-such-profile"), {})
    assert exc_info.value.code == "slot.profile_missing"
    assert exc_info.value.details["profile"] == "no-such-profile"
