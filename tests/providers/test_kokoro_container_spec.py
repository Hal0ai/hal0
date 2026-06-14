"""Kokoro TTS container spec (Phase B)."""

from typing import Any

from hal0.providers.container import _render_unit_from_spec
from hal0.providers.kokoro import KokoroProvider


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "tts",
        "port": 8084,
        "device": "cpu",
        "type": "tts",
        "runtime": "container",
        "profile": "tts",
        "model": {"default": "kokoro-v1"},
    }
    base.update(overrides)
    return base


def test_spec_has_no_gpu_devices_or_groups() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert spec.devices == []
    assert spec.group_add == []


def test_spec_command_carries_port_host_and_model_path() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8084"
    assert c[c.index("--host") + 1] == "0.0.0.0"
    assert c[c.index("--model_path") + 1] == "/mnt/ai-models/local/kokoro-v1/kokoro-onnx"


def test_spec_mounts_model_store_and_publishes_loopback() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    # ":ro" is appended to the container dst — that's how ContainerSpec
    # expresses read-only mounts (_render_unit_from_spec emits --volume={src}:{dst}).
    assert any(m[0] == "/mnt/ai-models" for m in spec.mounts)
    assert spec.port == 8084
    assert spec.network_mode == ""


def test_spec_security_opts_for_lxc() -> None:
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    assert "apparmor=unconfined" in spec.security_opt
    assert "seccomp=unconfined" in spec.security_opt


def test_spec_ro_mount_dst_has_ro_suffix() -> None:
    """ContainerSpec mount dst must carry :ro (+ SELinux z) so the rendered
    --volume arg is read-only and relabelled on SELinux hosts."""
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    ai_mount = next(m for m in spec.mounts if m[0] == "/mnt/ai-models")
    assert ai_mount[1].endswith(":ro,z"), (
        f"mount dst {ai_mount[1]!r} must end with ':ro,z' — "
        "_render_unit_from_spec emits --volume={src}:{dst} verbatim"
    )


# ── Renderer integration test ──────────────────────────────────────────────────


def test_renderer_no_device_args_publish_volume_command() -> None:
    """_render_unit_from_spec renders the kokoro spec correctly.

    Checks:
    - No --device= args (CPU-only slot).
    - --publish=127.0.0.1:8084:8084 present.
    - Volume arg with /mnt/ai-models present.
    - Command tail contains --model_path and --port.
    """
    spec = KokoroProvider().container_spec(_slot_cfg(), {})
    unit = _render_unit_from_spec("tts", spec, runtime_bin="/usr/bin/docker")

    # Flatten the ExecStart line for easy assertion.
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))

    assert "--device=" not in exec_line, "CPU slot must not pass any --device= args"
    assert "--publish=127.0.0.1:8084:8084" in exec_line
    # Exact rendered token — pins the 3-colon podman ro syntax; a substring
    # check would also pass on a malformed 4-colon render.
    assert "--volume=/mnt/ai-models:/mnt/ai-models:ro" in exec_line
    assert "--model_path" in exec_line
    assert "--port" in exec_line
    assert "8084" in exec_line


def test_slot_port_override_wins() -> None:
    """Slot port overrides the 8084 default end-to-end (spec + render)."""
    spec = KokoroProvider().container_spec(_slot_cfg(port=8097), {})
    c = spec.command
    assert c[c.index("--port") + 1] == "8097"
    assert spec.port == 8097

    unit = _render_unit_from_spec("tts", spec, runtime_bin="/usr/bin/docker")
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert "--publish=127.0.0.1:8097:8097" in exec_line
