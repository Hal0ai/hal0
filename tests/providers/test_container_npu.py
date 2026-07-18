"""ContainerProvider NPU branch: spec-rendered units + FLM health fallback (Phase A)."""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hal0.providers.base import ContainerSpec
from hal0.providers.container import ContainerProvider, _render_quadlet_from_plan

_TEST_RUNTIME = "/usr/bin/docker"


def _flm_spec(**overrides: Any) -> ContainerSpec:
    base = dict(
        image="ghcr.io/hal0ai/hal0-toolbox-flm:v1",
        command=[
            "serve",
            "gemma3:4b",
            "--host",
            "0.0.0.0",
            "--port",
            "8088",
            "--ctx-len",
            "16384",
        ],
        env={
            "LD_LIBRARY_PATH": "/opt/fastflowlm/lib:/opt/xilinx/xrt/lib:/usr/lib/x86_64-linux-gnu"
        },
        mounts=[("/var/lib/hal0/.config/flm/models", "/var/lib/hal0/.config/flm/models")],
        devices=["/dev/accel/accel0", "/dev/dri/renderD128"],
        cap_add=[],
        security_opt=["apparmor=unconfined", "seccomp=unconfined"],
        group_add=["993"],
        port=8088,
        network_mode="",
        extra_args=["--ulimit memlock=-1"],
    )
    base.update(overrides)
    return ContainerSpec(**base)


def _render_from_spec(
    token: str,
    spec: ContainerSpec,
    *,
    runtime_bin: str | None = None,
    publish_host: str = "127.0.0.1",
) -> str:
    """Shim mirroring the deleted ``_render_unit_from_spec`` alias.

    Accepts (and ignores) ``runtime_bin`` — the Quadlet renderer is podman-only,
    so the runtime binary is no longer a render input.
    """
    return _render_quadlet_from_plan(token, spec, publish_host=publish_host)


def _exec(unit_text: str) -> list[str]:
    """Return the in-container argv from the Quadlet ``Exec=`` line."""
    for line in unit_text.splitlines():
        if line.startswith("Exec="):
            return shlex.split(line[len("Exec=") :])
    raise AssertionError("Exec not found")


class TestRenderUnitFromSpec:
    def test_devices_and_mounts_in_argv(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        lines = unit.splitlines()
        assert "AddDevice=/dev/accel/accel0" in lines
        assert "AddDevice=/dev/dri/renderD128" in lines
        assert "AddDevice=/dev/kfd" not in lines  # NPU != ROCm compute
        assert "Volume=/var/lib/hal0/.config/flm/models:/var/lib/hal0/.config/flm/models" in lines

    def test_command_env_memlock(self) -> None:
        spec = _flm_spec()
        unit = _render_from_spec("npu", spec, runtime_bin=_TEST_RUNTIME)
        lines = unit.splitlines()
        # Exec= is the in-container argv only (no image / runtime preamble).
        assert _exec(unit) == list(spec.command)
        # extra_args ``--ulimit memlock=-1`` passes through PodmanArgs= verbatim.
        assert "PodmanArgs=--ulimit memlock=-1" in lines
        assert any(line.startswith("Environment=LD_LIBRARY_PATH=") for line in lines)

    def test_unit_name_matches_template(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "ContainerName=hal0-slot-npu" in unit.splitlines()

    def test_security_opts_included(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "--security-opt apparmor=unconfined" in unit
        assert "--security-opt seccomp=unconfined" in unit
        assert "SecurityOpt=" not in unit

    def test_group_add_included(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "--group-add 993" in unit
        assert "GroupAdd=" not in unit

    def test_loopback_publish_derived_from_spec_port(self) -> None:
        """PublishPort is rendered declaratively from spec.port, not extra_args."""
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "PublishPort=127.0.0.1:8088:8088" in unit.splitlines()

    def test_network_mode_host_rendered(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(network_mode="host"), runtime_bin=_TEST_RUNTIME)
        lines = unit.splitlines()
        assert "Network=host" in lines
        # publish is meaningless under host networking — must be skipped
        assert not any(line.startswith("PublishPort=") for line in lines)

    def test_cap_add_rendered(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(cap_add=["SYS_NICE"]), runtime_bin=_TEST_RUNTIME)
        assert "AddCapability=SYS_NICE" in unit.splitlines()

    def test_unit_has_service_section(self) -> None:
        unit = _render_from_spec("npu", _flm_spec(), runtime_bin=_TEST_RUNTIME)
        assert "[Unit]" in unit
        assert "[Service]" in unit


class TestLoadSyncNpuBranch:
    def test_npu_slot_routes_through_flm_spec(self, tmp_path) -> None:
        provider = ContainerProvider()
        slot_cfg = {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "profile": "flm",
            "model": {"default": "gemma3:4b"},
            "npu": {"asr": True, "embed": False},
        }
        unit_file = tmp_path / "hal0-slot@npu.service"

        calls_made: list[list[str]] = []

        def fake_run(*args: str, check: bool = True) -> MagicMock:
            calls_made.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._container_runtime", return_value=_TEST_RUNTIME),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(slot_cfg, {"_model_key": "gemma3:4b"})

        unit_text = unit_file.read_text()
        lines = unit_text.splitlines()
        argv = _exec(unit_text)
        assert "AddDevice=/dev/accel/accel0" in lines
        assert "--asr" in argv
        # Must NOT call _resolve_profile (NPU path bypasses profile lookup)
        assert "AddDevice=/dev/kfd" not in lines

    def test_npu_slot_calls_systemctl_restart(self, tmp_path) -> None:
        provider = ContainerProvider()
        slot_cfg = {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "model": {"default": "gemma3:4b"},
            "npu": {"asr": False, "embed": False},
        }
        unit_file = tmp_path / "hal0-slot@npu.service"
        calls_made: list[list[str]] = []

        def fake_run(*args: str, check: bool = True) -> MagicMock:
            calls_made.append(list(args))
            m = MagicMock()
            m.returncode = 0
            return m

        with (
            patch("hal0.providers.container._container_runtime", return_value=_TEST_RUNTIME),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(slot_cfg, {"_model_key": "gemma3:4b"})

        cmds = [" ".join(c) for c in calls_made]
        assert any("daemon-reload" in c for c in cmds)
        assert any("restart" in c for c in cmds)

    def test_npu_slot_without_tag_raises(self) -> None:
        """No flm_tag / _model_key / [model].default → loud ValueError, never
        a silent fall-through to FLM's legacy default tag."""
        provider = ContainerProvider()
        slot_cfg = {
            "name": "npu",
            "port": 8088,
            "device": "npu",
            "runtime": "container",
            "model": {},
            "npu": {"asr": False, "embed": False},
        }
        with pytest.raises(ValueError, match="no FLM model tag"):
            provider.load_sync(slot_cfg, {})

    def test_gpu_slot_unaffected_by_npu_branch(self, tmp_path) -> None:
        """device=gpu-rocm still renders a llama-server unit (not FLM)."""
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
            # GPU device nodes are existence-filtered at the container_spec call
            # site; force present so the CI host (no /dev/kfd) still renders them.
            patch("hal0.providers.container.os.path.exists", return_value=True),
            patch.object(provider, "_run", side_effect=fake_run),
            patch.object(provider, "_unit_path", return_value=unit_file),
        ):
            provider.load_sync(
                {
                    "name": "chat",
                    "port": 8095,
                    "profile": "rocm",
                    "device": "gpu-rocm",
                },
                {"path": "/mnt/ai-models/model.gguf", "_model_key": "my-model"},
            )

        unit_text = unit_file.read_text()
        # GPU path: llama-server args present in the in-container argv
        assert "--model" in _exec(unit_text)
        # GPU path: /dev/kfd present, /dev/accel/accel0 absent
        assert "AddDevice=/dev/kfd" in unit_text.splitlines()
        assert "/dev/accel/accel0" not in unit_text


@pytest.mark.anyio
async def test_health_falls_back_to_v1_models() -> None:
    """FLM has no /health; 404 on /health + 200 on /v1/models → healthy."""
    provider = ContainerProvider()

    health_resp = MagicMock()
    health_resp.status_code = 404

    models_resp = MagicMock()
    models_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/health"):
            return health_resp
        if url.endswith("/v1/models"):
            return models_resp
        raise AssertionError(f"unexpected URL: {url}")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = fake_get

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health(8088)

    assert result["ok"] is True
    assert result["status"] == "healthy"


@pytest.mark.anyio
async def test_health_connect_refused_stays_unhealthy() -> None:
    """Connection-refused (no container) stays unhealthy even with fallback."""
    import httpx

    provider = ContainerProvider()

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        raise httpx.ConnectError("Connection refused")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = fake_get

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health(8088)

    assert result["ok"] is False


@pytest.mark.anyio
async def test_health_200_on_health_still_healthy() -> None:
    """Existing /health→200 path still works (GPU slots use it)."""
    provider = ContainerProvider()

    health_resp = MagicMock()
    health_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return health_resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = fake_get

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health(8095)

    assert result["ok"] is True
    assert result["status"] == "healthy"


@pytest.mark.anyio
async def test_health_delegates_to_flm_tier1_when_slot_is_npu() -> None:
    """When ContainerProvider.health is given an FLM slot_cfg, it delegates to
    FLMProvider.health (the cheap non-empty /v1/models liveness probe — no NPU
    sentinel on the hot path) instead of the weak any-200 /v1/models fallback."""
    from hal0.providers.flm import FLMProvider

    sentinel = {"ok": True, "status": "ready", "model": "qwen3:0.6b"}

    async def fake_flm_health(self: Any, port: int) -> dict[str, Any]:
        assert port == 8088
        return sentinel

    provider = ContainerProvider()
    with (
        patch.object(FLMProvider, "health", fake_flm_health),
        # /health must NOT be probed on the delegation path.
        patch(
            "hal0.providers.container.httpx.AsyncClient",
            side_effect=AssertionError("must not open an httpx client when delegating to FLM"),
        ),
    ):
        result = await provider.health(8088, slot_cfg={"device": "npu"})

    assert result is sentinel


@pytest.mark.anyio
async def test_health_no_delegation_without_slot_cfg() -> None:
    """The legacy port-only call keeps the /health + /v1/models fallback path
    (no FLM delegation, so existing manager call sites are unaffected)."""
    provider = ContainerProvider()

    health_resp = MagicMock()
    health_resp.status_code = 200

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return health_resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = fake_get

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health(8095)

    assert result["ok"] is True


@pytest.mark.anyio
async def test_health_v1_models_also_fails_unhealthy() -> None:
    """404 on /health AND 503 on /v1/models → unhealthy."""
    provider = ContainerProvider()

    bad_resp = MagicMock()
    bad_resp.status_code = 503

    async def fake_get(url: str, **kwargs: Any) -> MagicMock:
        return bad_resp

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = fake_get

    with patch("hal0.providers.container.httpx.AsyncClient", return_value=mock_client):
        result = await provider.health(8088)

    assert result["ok"] is False
