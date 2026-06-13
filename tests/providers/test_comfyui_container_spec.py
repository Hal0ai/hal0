"""ComfyUI container spec — live-parity with the validated CT105 deployment.

Phase D task D2. The spec must replicate what `docker inspect comfyui`
showed working on CT105: kyuz0 image (ComfyUI at /opt/ComfyUI, venv at
/opt/venv), bash -lc cd+exec argv, /mnt/ai-models/comfyui data mounts,
--ipc=host (host /dev/shm serves Wan/Hunyuan video; podman rejects
--shm-size with host IPC, unlike docker), host networking,
and label=disable alongside the usual LXC security opts.
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.providers.comfyui import _HAL0_COMFYUI_IMAGE, ComfyUIProvider
from hal0.providers.container import _render_unit_from_spec, _spec_provider_for

_GPU_NODES = ["/dev/kfd", "/dev/dri/card1", "/dev/dri/renderD128"]


def _img_cfg(**overrides: Any) -> dict[str, Any]:
    """Slot cfg shaped like the loaded installer/etc-hal0/slots/img.toml."""
    base: dict[str, Any] = {
        "name": "img",
        "type": "image",
        "provider": "comfyui",
        "device": "gpu-rocm",
        "runtime": "container",
        "profile": "comfyui",
        "enabled": True,
        "port": 8188,
        "model": {"default": "sdxl-turbo"},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _gpu_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin GPU device enumeration — dev/CI boxes have no /dev/kfd."""
    monkeypatch.setattr(
        "hal0.providers.comfyui.resolve_gpu_device_paths",
        lambda: list(_GPU_NODES),
    )


# ── container_spec live parity ────────────────────────────────────────────────


def test_comfyui_spec_matches_live_deployment() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    # Devices via resolve_gpu_device_paths() — explicit nodes (podman, #674).
    assert spec.devices and "/dev/kfd" in spec.devices
    assert spec.devices == _GPU_NODES
    # Data mounts mirror docker inspect on CT105 (first-class Mount objects).
    pairs = {(m.source, m.target) for m in spec.mounts}
    assert ("/mnt/ai-models/comfyui/models", "/root/comfy-models") in pairs
    for sub in ("output", "input", "user", "custom_nodes"):
        assert (f"/mnt/ai-models/comfyui/{sub}", f"/opt/ComfyUI/{sub}") in pairs
    assert any("extra_model_paths.yaml" in m.source for m in spec.mounts)
    # Wan/Hunyuan video models need shared memory.
    assert "--ipc=host" in spec.extra_args
    # podman 125s on --shm-size with --ipc=host ("cannot set shmsize when
    # running in the host IPC Namespace") — docker ignored the combo. Host
    # /dev/shm (63G on CT105) serves the video models directly.
    assert not any(a.startswith("--shm-size") for a in spec.extra_args)
    assert set(spec.security_opt) == {
        "seccomp=unconfined",
        "apparmor=unconfined",
        "label=disable",
    }
    assert spec.network_mode == "host"
    assert spec.port == 8188
    # Profile flags flow into the bash -lc payload.
    assert spec.command[-1].endswith("--cache-none")


def test_comfyui_argv_uses_opt_comfyui_workdir() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    assert spec.command[:2] == ["bash", "-lc"]
    payload = spec.command[2]
    assert "/opt/ComfyUI" in payload
    assert "--port 8188" in payload
    assert payload.startswith("cd /opt/ComfyUI && exec python main.py")
    assert "--listen 0.0.0.0" in payload


def test_comfyui_extra_model_paths_mount_is_read_only() -> None:
    """extra_model_paths.yaml is read-only via the first-class Mount flag; the
    bare target carries no ":ro" (the renderer appends it)."""
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    yaml_mounts = [m for m in spec.mounts if "extra_model_paths.yaml" in m.source]
    assert len(yaml_mounts) == 1
    yaml_mount = yaml_mounts[0]
    assert yaml_mount.source == "/mnt/ai-models/comfyui/extra_model_paths.yaml"
    assert yaml_mount.target == "/opt/ComfyUI/extra_model_paths.yaml"
    assert yaml_mount.read_only is True
    assert yaml_mount.render() == (
        "/mnt/ai-models/comfyui/extra_model_paths.yaml:/opt/ComfyUI/extra_model_paths.yaml:ro"
    )


def test_comfyui_data_root_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", "/srv/comfy-data")
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    pairs = {(m.source, m.target) for m in spec.mounts}
    assert ("/srv/comfy-data/models", "/root/comfy-models") in pairs
    assert ("/srv/comfy-data/output", "/opt/ComfyUI/output") in pairs
    assert not any(m.source.startswith("/mnt/ai-models/comfyui") for m in spec.mounts)


def test_comfyui_profile_flags_fallback_without_profile() -> None:
    """No resolvable profile → live-validated default flag bundle."""
    cfg = _img_cfg()
    del cfg["profile"]
    spec = ComfyUIProvider().container_spec(cfg, {})
    assert spec.command[2].endswith("--disable-mmap --bf16-vae --cache-none")


def test_comfyui_slot_port_override_flows_into_argv() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(port=8189), {})
    assert spec.port == 8189
    assert "--port 8189" in spec.command[2]


def test_comfyui_fallback_image_is_kyuz0() -> None:
    """D1 review: the last-resort fallback must not point at an unpublished image."""
    assert _HAL0_COMFYUI_IMAGE == "docker.io/kyuz0/amd-strix-halo-comfyui:latest"


# ── renderer integration ──────────────────────────────────────────────────────


def test_renderer_host_network_skips_publish_and_keeps_shm() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    unit = _render_unit_from_spec("img", spec, runtime_bin="/usr/bin/podman")
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))

    assert "--network=host" in exec_line
    assert "--publish" not in exec_line, "host networking must not publish ports"
    assert "--ipc=host" in exec_line
    assert "--shm-size" not in exec_line
    assert "--security-opt=label=disable" in exec_line
    assert "--device=/dev/kfd" in exec_line
    assert (
        "--volume=/mnt/ai-models/comfyui/extra_model_paths.yaml"
        ":/opt/ComfyUI/extra_model_paths.yaml:ro" in exec_line
    )


# ── _spec_provider_for dispatch ───────────────────────────────────────────────


def test_spec_provider_for_dispatches_comfyui() -> None:
    assert isinstance(_spec_provider_for({"provider": "comfyui", "type": "image"}), ComfyUIProvider)
    assert isinstance(_spec_provider_for({"profile": "comfyui"}), ComfyUIProvider)
    assert isinstance(_spec_provider_for({"type": "image"}), ComfyUIProvider)


def test_image_section_dict_is_not_an_image_override(monkeypatch) -> None:
    """The [image] TOML section (#599 settings) arrives in raw slot dicts under
    the same key as the per-slot image-ref OVERRIDE string. A dict must never
    be treated as an image ref (live CT105 regression: ExecStart rendered
    str(dict) -> podman 'invalid reference format', hal0-slot@img exit 125)."""
    from hal0.providers.comfyui import ComfyUIProvider

    cfg = {
        "name": "img",
        "port": 8188,
        "profile": "comfyui",
        "provider": "comfyui",
        "image": {"idle_restore_minutes": 5, "default_size": "1024x1024"},
    }
    ref = ComfyUIProvider().image_ref(cfg)
    assert isinstance(ref, str)
    assert "idle_restore_minutes" not in ref
    assert "kyuz0/amd-strix-halo-comfyui" in ref  # manifest pin or fallback tag


def test_llama_image_section_dict_not_override() -> None:
    from hal0.providers.llama_server import LlamaServerProvider

    cfg = {"name": "x", "port": 8081, "profile": "vulkan", "image": {"idle_restore_minutes": 0}}
    ref = LlamaServerProvider().image_ref(cfg)
    assert isinstance(ref, str) and "idle_restore_minutes" not in ref
