"""ComfyUI container spec — live-parity with the validated CT105 deployment.

Phase D task D2. The spec must replicate what `docker inspect comfyui`
showed working on CT105: the kyuz0 image layout that hal0's own
ghcr.io/hal0ai/hal0-runner-comfyui mirrors (ComfyUI at /opt/ComfyUI, venv at
/opt/venv), bash -lc cd+exec argv, /mnt/ai-models/comfyui data mounts,
--ipc=host (host /dev/shm serves Wan/Hunyuan video; podman rejects
--shm-size with host IPC, unlike docker), host networking,
and label=disable alongside the usual LXC security opts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.providers.comfyui import ComfyUIProvider, provision_data_root
from hal0.providers.container import _render_quadlet_from_plan, _spec_provider_for

_GPU_NODES = ["/dev/kfd", "/dev/dri/card1", "/dev/dri/renderD128"]


def _render_from_spec(token, spec, *, runtime_bin=None, publish_host="127.0.0.1"):
    """Quadlet renderer shim (ignores the legacy runtime_bin kwarg)."""
    return _render_quadlet_from_plan(token, spec, publish_host=publish_host)


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


@pytest.fixture(autouse=True)
def _pin_model_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """This module asserts on literal /mnt/ai-models/comfyui/* mount
    sources — pin the resolver to it (ML-3's unified store.store_root()
    default is now paths.models_dir(), not /mnt/ai-models)."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


@pytest.fixture(autouse=True)
def _no_data_root_provisioning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building a spec must not touch the literal paths these tests assert on.

    ``container_spec`` provisions the data root; against ``/mnt/ai-models`` or
    ``/srv/comfy-data`` that would create directories and write files on any
    box where they are writable. Provisioning itself is tested against
    ``tmp_path`` in test_comfyui_provisioning.py.
    """
    monkeypatch.setattr("hal0.providers.comfyui.provision_data_root", lambda: None)


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
    assert spec.ipc == "host"
    # podman 125s on --shm-size with --ipc=host ("cannot set shmsize when
    # running in the host IPC Namespace") — docker ignored the combo. Host
    # /dev/shm (63G on CT105) serves the video models directly.
    assert "--shm-size" not in _render_from_spec("img", spec)
    assert set(spec.security_opt) == {
        "seccomp=unconfined",
        "apparmor=unconfined",
        "label=disable",
    }
    assert spec.network_mode == "host"
    assert spec.port == 8188
    # Profile flags flow into the bash -lc payload.
    assert spec.command[-1].endswith("--cache-none")


def _gate_env(spec: Any) -> dict[str, str]:
    return {k: v for k, v in spec.env.items() if k.startswith("HAL0_COMFYUI_")}


@pytest.fixture
def _api_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("HAL0_PORT", "HAL0_BIND_HOST", "HAL0_COMFYUI_GATE_FAIL_OPEN"):
        monkeypatch.delenv(key, raising=False)


def test_comfyui_spec_points_the_gate_at_hal0_api_by_default(_api_env_unset: None) -> None:
    """The gate reaches hal0-api through the unit's environment; with no
    api.env settings that is loopback on the default port, and no opt-out."""
    env = _gate_env(ComfyUIProvider().container_spec(_img_cfg(), {}))
    assert env["HAL0_COMFYUI_STATUS_URL"] == "http://127.0.0.1:8080/api/comfyui/arbiter"
    assert env["HAL0_COMFYUI_SWITCHOVER_URL"] == "http://127.0.0.1:8080/api/comfyui/switchover"
    assert env["HAL0_COMFYUI_HANDOFF_URL"] == "http://127.0.0.1:8080/api/comfyui/prompt-handoff"
    assert "HAL0_COMFYUI_GATE_FAIL_OPEN" not in env


def test_comfyui_spec_points_the_gate_at_hal0_api_on_its_own_port(
    monkeypatch: pytest.MonkeyPatch, _api_env_unset: None
) -> None:
    """HAL0_PORT is an installer option: a box whose API is not on 8080 must
    not have its gate refuse every image as hal0-unreachable."""
    monkeypatch.setenv("HAL0_PORT", "9123")
    monkeypatch.setenv("HAL0_BIND_HOST", "0.0.0.0")
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    env = _gate_env(spec)
    assert env["HAL0_COMFYUI_STATUS_URL"] == "http://127.0.0.1:9123/api/comfyui/arbiter"
    assert env["HAL0_COMFYUI_SWITCHOVER_URL"] == "http://127.0.0.1:9123/api/comfyui/switchover"
    unit = _render_from_spec("img", spec)
    assert "Environment=HAL0_COMFYUI_STATUS_URL=http://127.0.0.1:9123/api/comfyui/arbiter" in unit


def test_comfyui_spec_dials_a_specific_bind_address(
    monkeypatch: pytest.MonkeyPatch, _api_env_unset: None
) -> None:
    """hal0-api bound to one address does not answer on loopback."""
    monkeypatch.setenv("HAL0_BIND_HOST", "192.0.2.10")
    env = _gate_env(ComfyUIProvider().container_spec(_img_cfg(), {}))
    assert env["HAL0_COMFYUI_STATUS_URL"] == "http://192.0.2.10:8080/api/comfyui/arbiter"
    monkeypatch.setenv("HAL0_BIND_HOST", "::1")
    env = _gate_env(ComfyUIProvider().container_spec(_img_cfg(), {}))
    assert env["HAL0_COMFYUI_STATUS_URL"] == "http://[::1]:8080/api/comfyui/arbiter"


def test_comfyui_spec_reads_the_gate_settings_from_api_env(
    _api_env_unset: None, tmp_hal0_home: str
) -> None:
    """Every unit render path carries the settings, including the installer's
    and hal0 update's re-render, which run without api.env in their
    environment: the values come from the file hal0-api itself loads."""
    api_env = Path(tmp_hal0_home) / "etc" / "hal0" / "api.env"
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text(
        '# hal0-api environment\nHAL0_PORT=9124\nHAL0_COMFYUI_GATE_FAIL_OPEN="yes"\n',
        encoding="utf-8",
    )
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    env = _gate_env(spec)
    assert env["HAL0_COMFYUI_STATUS_URL"] == "http://127.0.0.1:9124/api/comfyui/arbiter"
    assert env["HAL0_COMFYUI_GATE_FAIL_OPEN"] == "1"
    assert "Environment=HAL0_COMFYUI_GATE_FAIL_OPEN=1" in _render_from_spec("img", spec)


def test_comfyui_spec_passes_the_gate_fail_open_switch_through(
    monkeypatch: pytest.MonkeyPatch, _api_env_unset: None
) -> None:
    """The hal0_gpu_gate node reads HAL0_COMFYUI_GATE_FAIL_OPEN inside the
    container; an operator sets it in hal0-api's environment (api.env) and the
    img slot's next start hands it on."""
    monkeypatch.setenv("HAL0_COMFYUI_GATE_FAIL_OPEN", "1")
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    assert _gate_env(spec)["HAL0_COMFYUI_GATE_FAIL_OPEN"] == "1"
    assert "Environment=HAL0_COMFYUI_GATE_FAIL_OPEN=1" in _render_from_spec("img", spec)


def test_comfyui_spec_mounts_the_gates_credential_read_only() -> None:
    """The gate's token (#3058) reaches the container as a read-only directory
    mount, never as an environment value the unit file would carry."""
    from hal0.comfyui import gate_credential

    spec = ComfyUIProvider().container_spec(_img_cfg(), {})

    token = gate_credential.read_token()
    assert token is not None
    gate_mounts = [m for m in spec.mounts if m.source == str(gate_credential.token_dir())]
    assert len(gate_mounts) == 1
    assert gate_mounts[0].target == "/opt/hal0-gate"
    assert gate_mounts[0].read_only is True
    assert _gate_env(spec)["HAL0_COMFYUI_GATE_TOKEN_FILE"] == "/opt/hal0-gate/token"
    unit = _render_from_spec("img", spec)
    assert token not in unit
    assert token not in repr(spec.env)


def test_comfyui_spec_keeps_the_gates_token_across_renders() -> None:
    from hal0.comfyui import gate_credential

    ComfyUIProvider().container_spec(_img_cfg(), {})
    first = gate_credential.read_token()
    ComfyUIProvider().container_spec(_img_cfg(), {})

    assert gate_credential.read_token() == first


def test_comfyui_spec_without_a_writable_credential_still_launches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token that cannot be written leaves the gate without a credential (as
    before #3058), not the img slot without a container."""
    from hal0.comfyui import gate_credential

    def _unwritable() -> str:
        raise PermissionError("read-only state root")

    monkeypatch.setattr(gate_credential, "ensure_token", _unwritable)

    spec = ComfyUIProvider().container_spec(_img_cfg(), {})

    assert all(m.target != "/opt/hal0-gate" for m in spec.mounts)
    assert "HAL0_COMFYUI_GATE_TOKEN_FILE" not in spec.env


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
    assert yaml_mount.render_quadlet() == (
        "/mnt/ai-models/comfyui/extra_model_paths.yaml:/opt/ComfyUI/extra_model_paths.yaml:ro"
    )


def test_comfyui_data_root_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", "/srv/comfy-data")
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    pairs = {(m.source, m.target) for m in spec.mounts}
    assert ("/srv/comfy-data/models", "/root/comfy-models") in pairs
    assert ("/srv/comfy-data/output", "/opt/ComfyUI/output") in pairs
    assert not any(m.source.startswith("/mnt/ai-models/comfyui") for m in spec.mounts)


def test_comfyui_named_missing_profile_refuses_to_launch() -> None:
    """A slot that names a profile the catalog lacks fails closed, as every
    llama-server slot does (ADR-0029) — it never launches the default tune."""
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity) as exc_info:
        ComfyUIProvider().container_spec(_img_cfg(profile="no-such-profile"), {})
    assert exc_info.value.code == "slot.profile_missing"
    assert exc_info.value.details == {"slot": "img", "profile": "no-such-profile"}


def test_comfyui_nested_slot_profile_missing_refuses_to_launch() -> None:
    """The ``[slot].profile`` spelling is the same named profile."""
    from hal0.errors import UnprocessableEntity

    cfg = _img_cfg()
    del cfg["profile"]
    cfg["slot"] = {"profile": "no-such-profile"}
    with pytest.raises(UnprocessableEntity) as exc_info:
        ComfyUIProvider().container_spec(cfg, {})
    assert exc_info.value.code == "slot.profile_missing"


def test_comfyui_profile_flags_default_without_profile() -> None:
    """A slot that names NO profile → live-validated default flag bundle."""
    cfg = _img_cfg()
    del cfg["profile"]
    spec = ComfyUIProvider().container_spec(cfg, {})
    assert spec.command[2].endswith("--disable-mmap --bf16-vae --cache-none")


def test_comfyui_slot_port_override_flows_into_argv() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(port=8189), {})
    assert spec.port == 8189
    assert "--port 8189" in spec.command[2]


def test_comfyui_image_is_the_hal0_runner_repo() -> None:
    """The ComfyUI image is the ``comfyui`` runner's pin (manifest.json supplies
    the tag/digest), never a third-party image."""
    ref = ComfyUIProvider().image_ref({})
    assert ref.startswith("ghcr.io/hal0ai/hal0-runner-comfyui")


# ── renderer integration ──────────────────────────────────────────────────────


def test_renderer_host_network_skips_publish_and_keeps_shm() -> None:
    spec = ComfyUIProvider().container_spec(_img_cfg(), {})
    unit = _render_from_spec("img", spec, runtime_bin="/usr/bin/podman")
    lines = unit.splitlines()

    assert "Network=host" in lines
    assert not any(line.startswith("PublishPort=") for line in lines), (
        "host networking must not publish ports"
    )
    # The typed ipc field renders through PodmanArgs= (no native Quadlet key).
    assert "PodmanArgs=--ipc=host" in lines
    assert "--shm-size" not in unit
    assert "--security-opt label=disable" in unit
    assert "AddDevice=/dev/kfd" in lines
    assert (
        "Volume=/mnt/ai-models/comfyui/extra_model_paths.yaml"
        ":/opt/ComfyUI/extra_model_paths.yaml:ro" in lines
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
        "image": {"default_size": "1024x1024"},
    }
    ref = ComfyUIProvider().image_ref(cfg)
    assert isinstance(ref, str)
    assert "1024x1024" not in ref
    assert "hal0ai/hal0-runner-comfyui" in ref  # the runner pin


def test_comfyui_container_spec_provisions_data_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing data dirs are created before the mounts are rendered.

    Podman refuses a bind mount whose host path is absent (statfs → exit
    125), which crash-looped the img slot on any box where the comfyui tree
    wasn't provisioned.
    """
    root = tmp_path / "comfy-data"
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(root))
    # This one test wants the real provisioning (the autouse fixture no-ops it).
    monkeypatch.setattr("hal0.providers.comfyui.provision_data_root", provision_data_root)
    ComfyUIProvider().container_spec(_img_cfg(), {})
    for sub in ("models", "output", "input", "user", "custom_nodes"):
        assert (root / sub).is_dir(), sub
    assert (root / "extra_model_paths.yaml").is_file()


@pytest.mark.asyncio
async def test_comfyui_wait_ready_uses_system_stats_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wait_ready resolves through the provider's own /system_stats health.

    The generic container waiter polls llama-style /health, which ComfyUI
    never serves — the slot wedged in WARMING behind a healthy server.
    """
    provider = ComfyUIProvider()
    answers = iter([{"ok": False, "status": "http_404"}, {"ok": True}])

    async def _fake_health(port: int) -> dict:
        return next(answers)

    monkeypatch.setattr(provider, "health", _fake_health)
    monkeypatch.setattr("hal0.providers.container._HEALTH_POLL_INTERVAL_S", 0.01)
    await provider.wait_ready(8188)  # returns without raising

    async def _never_ok(port: int) -> dict:
        return {"ok": False, "status": "http_404"}

    monkeypatch.setattr(provider, "health", _never_ok)
    monkeypatch.setattr("hal0.providers.container._HEALTH_TIMEOUT_S", 0.05)
    with pytest.raises(TimeoutError):
        await provider.wait_ready(8188)
