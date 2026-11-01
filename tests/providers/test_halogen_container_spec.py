"""halogen (Flash-Next) container spec, env mapping, readiness and sentinel.

The provider turns the slot's effective tune plus the profile's
``HALOGEN_NAME=value`` flag tokens into one launch plan. Everything the engine
reads is an environment variable, so the env mapping carries most of the
assertions: hal0-managed facts always set and last, tune-owned variables from
``effective_tune``, profile tokens first and shadowed by both.
"""

from __future__ import annotations

import asyncio
import shlex
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import Hal0Error
from hal0.install.perms import ensure_shared_dir as real_ensure_shared_dir
from hal0.profiles import ProfileCatalog
from hal0.providers import halogen as halogen_module
from hal0.providers.container import (
    ContainerProvider,
    _render_quadlet_from_plan,
    _spec_provider_for,
    readiness_probe_budget_s,
    runner_family_mismatch,
)
from hal0.providers.halogen import (
    VISION_SIDECAR,
    HalogenProvider,
    halogen_env,
    vision_sidecar_present,
)
from hal0.slots import output_sanity
from hal0.slots.state import RunnerFamilyMismatch
from hal0.slots.tune import EffectiveTune

CKPT = "/mnt/ai-models/halogen-flash-next/qwen38-flash-next-ht43.hgn"
_FAKE_DEVICES = ["/dev/kfd", "/dev/dri/renderD128", "/dev/dri/amdgpu"]
_FAKE_GIDS = [993, 44]


def _cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "flash",
        "port": 8099,
        "device": "gpu-rocm",
        "type": "llm",
        "runtime": "container",
        "profile": "halogen",
        "model": {"default": "qwen3.8-flash-next-ht43"},
    }
    base.update(overrides)
    return base


def _info() -> dict[str, Any]:
    return {"_model_key": "qwen3.8-flash-next-ht43", "path": CKPT, "backends": ["halogen"]}


@pytest.fixture(autouse=True)
def _pin_model_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mount assertions name the literal /mnt/ai-models store."""
    monkeypatch.setenv("HAL0_MODEL_STORE", "/mnt/ai-models")


@pytest.fixture(autouse=True)
def _stub_gpu(tmp_hal0_home: str) -> Any:
    """Deterministic GPU passthrough, and an isolated HAL0_HOME so the seeded
    ``halogen`` profile resolves from the shipped seeds rather than the host's
    /etc/hal0/profiles.toml."""
    with (
        patch(
            "hal0.providers.halogen.resolve_gpu_device_paths",
            return_value=list(_FAKE_DEVICES),
        ),
        patch(
            "hal0.providers.halogen.resolve_gpu_group_ids",
            return_value=list(_FAKE_GIDS),
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def _cache_dir_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Keep every spec build off the real filesystem: the provider creates the
    ``/cache`` bind source at spec time, and the default one is under /var/lib."""
    calls: list[str] = []
    monkeypatch.setattr(halogen_module, "ensure_shared_dir", lambda path: calls.append(str(path)))
    return calls


def _tune(**overrides: Any) -> EffectiveTune:
    values: dict[str, Any] = {
        "name": "halogen",
        "flags": "",
        "runner": "halogen",
        "ctx_size": None,
        "enable_thinking": None,
        "reasoning_effort": "default",
        "reasoning_budget": -1,
        "vision": "off",
        "speculation": "auto",
        "sources": {},
    }
    values.update(overrides)
    return EffectiveTune(**values)


# ── Launch plan shape ───────────────────────────────────────────────────────


def test_spec_shape() -> None:
    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert spec.command == ["all"]
    assert spec.ipc == "host"
    assert spec.ulimits == ["memlock=-1"]
    assert spec.stop_timeout_s == 120
    assert spec.restart_prevent_exit_status == (1, 2)
    assert spec.port == 8099
    assert spec.network_mode == ""
    assert spec.devices == _FAKE_DEVICES
    assert spec.group_add == ["993", "44"]
    assert [m.target for m in spec.mounts] == ["/mnt/ai-models", "/cache"]
    assert spec.mounts[0].read_only


def test_checkpoint_outside_the_store_mounts_its_directory() -> None:
    """The engine reads the tokenizer, lookup table and vision sidecar beside the
    checkpoint, so a model file the store root does not cover gets its own
    directory mounted, read-only and at the same path."""
    info = {**_info(), "path": "/srv/halogen/qwen38-flash-next-ht43.hgn"}
    spec = HalogenProvider().container_spec(_cfg(), info)
    assert [m.target for m in spec.mounts] == ["/mnt/ai-models", "/srv/halogen", "/cache"]
    extra = spec.mounts[1]
    assert extra.source == "/srv/halogen"
    assert extra.read_only is True
    assert spec.mounts[2].read_only is False


def test_checkpoint_under_the_store_adds_no_extra_mount() -> None:
    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert [m.target for m in spec.mounts] == ["/mnt/ai-models", "/cache"]


def test_spec_security_and_cache_mount() -> None:
    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert spec.security_opt == ["apparmor=unconfined", "seccomp=unconfined"]
    assert spec.cap_add == []
    cache = spec.mounts[1]
    assert cache.source == "/var/lib/hal0/halogen-cache"
    assert cache.read_only is False
    assert cache.selinux == "z"


def test_cache_dir_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_HALOGEN_CACHE", "/srv/halogen-cache")
    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert spec.mounts[1].source == "/srv/halogen-cache"
    assert spec.mounts[1].target == "/cache"


def test_spec_asks_for_the_cache_dir_it_mounts(
    monkeypatch: pytest.MonkeyPatch, _cache_dir_calls: list[str]
) -> None:
    HalogenProvider().container_spec(_cfg(), _info())
    assert _cache_dir_calls == ["/var/lib/hal0/halogen-cache"]
    monkeypatch.setenv("HAL0_HALOGEN_CACHE", "/srv/halogen-cache")
    HalogenProvider().container_spec(_cfg(), _info())
    assert _cache_dir_calls[-1] == "/srv/halogen-cache"


def test_spec_creates_the_cache_dir_for_real(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """podman exits 125 on a bind source that does not exist, so the spec
    creates it (shared-directory mode) instead of leaving the slot to fail."""
    cache = tmp_path / "state" / "halogen-cache"
    monkeypatch.setattr(halogen_module, "ensure_shared_dir", real_ensure_shared_dir)
    monkeypatch.setenv("HAL0_HALOGEN_CACHE", str(cache))
    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert cache.is_dir()
    assert spec.mounts[-1].source == str(cache)
    # A second render leaves the existing dir alone.
    HalogenProvider().container_spec(_cfg(), _info())
    assert cache.is_dir()


def test_spec_survives_an_uncreatable_cache_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A path under a regular file cannot be created; the spec still builds and
    the log names the path, so the later podman failure has a visible cause."""
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    monkeypatch.setattr(halogen_module, "ensure_shared_dir", real_ensure_shared_dir)
    monkeypatch.setenv("HAL0_HALOGEN_CACHE", str(blocker / "cache"))
    with caplog.at_level("WARNING", logger="hal0.providers.halogen"):
        spec = HalogenProvider().container_spec(_cfg(), _info())
    assert spec.mounts[-1].source == str(blocker / "cache")
    assert any(
        "halogen.cache_dir_unavailable" in r.getMessage() and str(blocker) in r.getMessage()
        for r in caplog.records
    )


def test_spec_health_probe_targets_the_slot_port() -> None:
    health = HalogenProvider().container_spec(_cfg(), _info()).health
    assert health is not None
    assert health.cmd.startswith("python3 -c ")
    assert "http://127.0.0.1:8099/health" in health.cmd
    assert "timeout=30" in health.cmd
    assert health.start_period == "10m"
    assert health.interval == "30s"
    assert health.retries == 3
    assert health.timeout == "35s"


def test_class_constants() -> None:
    provider = HalogenProvider()
    assert provider.name == "halogen"
    assert provider.gpu_runtime_needs_rocm is True
    assert provider.stop_grace_s == 120
    assert HalogenProvider.READY_TIMEOUT_S == 600.0
    assert HalogenProvider.READY_POLL_S == 5.0
    assert HalogenProvider.SENTINEL_TIMEOUT_S == 60.0
    assert HalogenProvider.CACHE_DIR == "/var/lib/hal0/halogen-cache"


def test_spec_refuses_a_model_row_without_a_path() -> None:
    with pytest.raises(Hal0Error) as exc_info:
        HalogenProvider().container_spec(_cfg(), {"_model_key": "qwen3.8-flash-next-ht43"})
    assert exc_info.value.code == "slot.model_missing"
    with pytest.raises(Hal0Error) as exc_info:
        HalogenProvider().container_spec(_cfg(), {"path": ""})
    assert exc_info.value.code == "slot.model_missing"


def test_spec_image_is_the_registry_pin() -> None:
    from hal0.runners import get_runner, resolve_runner_image

    spec = HalogenProvider().container_spec(_cfg(), _info())
    assert spec.image == resolve_runner_image(get_runner("halogen"))


# ── Environment ─────────────────────────────────────────────────────────────


def test_env_managed_facts() -> None:
    env = HalogenProvider().container_spec(_cfg(), _info()).env
    assert env["HALOGEN_API_PORT"] == "8099"
    assert env["HALOGEN_BIND"] == "127.0.0.1"
    assert env["HALOGEN_CHECKPOINT"] == CKPT
    assert env["HALOGEN_MODEL_ID"] == "qwen3.8-flash-next-ht43"
    assert "HALOGEN_DOWNLOAD" not in env
    assert "HALOGEN_API_KEY" not in env
    assert "HALOGEN_API_KEY_FILE" not in env
    assert not [k for k in env if k.startswith("HALOGEN_NPU_")]


def test_model_id_falls_back_to_the_slot_default() -> None:
    info = {"path": CKPT}
    env = HalogenProvider().container_spec(_cfg(), info).env
    assert env["HALOGEN_MODEL_ID"] == "qwen3.8-flash-next-ht43"


def test_env_seed_profile_flags_and_ctx() -> None:
    env = HalogenProvider().container_spec(_cfg(), _info()).env
    assert env["HALOGEN_KV_POOL_POSITIONS"] == "262144"
    assert env["HALOGEN_KV_SLOTS"] == "2"
    assert env["HALOGEN_MAX_TOK"] == "16384"
    assert env["HALOGEN_WEIGHTS_LOCK"] == "1"
    assert env["HALOGEN_CTX"] == "262144"


@pytest.mark.parametrize(
    ("tune", "expected", "absent"),
    [
        ({"enable_thinking": False}, {"HALOGEN_ENABLE_THINKING": "0"}, []),
        ({"enable_thinking": True}, {"HALOGEN_ENABLE_THINKING": "1"}, []),
        ({"reasoning_effort": "low"}, {"HALOGEN_REASONING_EFFORT": "low"}, []),
        ({"reasoning_effort": "default"}, {}, ["HALOGEN_REASONING_EFFORT"]),
        ({"reasoning_budget": 4096}, {"HALOGEN_MAX_THINKING_TOKENS": "4096"}, []),
        ({"reasoning_budget": -1}, {}, ["HALOGEN_MAX_THINKING_TOKENS"]),
        ({"speculation": "off"}, {"HALOGEN_DRAFTER_DEFAULT": "0", "HALOGEN_PLD": "0"}, []),
        ({"speculation": "auto"}, {}, ["HALOGEN_DRAFTER_DEFAULT", "HALOGEN_PLD"]),
        ({"ctx_size": 131072}, {"HALOGEN_CTX": "131072"}, []),
    ],
)
def test_tune_mapping(tune: dict[str, Any], expected: dict[str, str], absent: list[str]) -> None:
    env = HalogenProvider().container_spec(_cfg(tune=tune), _info()).env
    for key, value in expected.items():
        assert env[key] == value
    for key in absent:
        assert key not in env


def test_unset_tune_leaves_thinking_to_the_template() -> None:
    env = halogen_env(port=8099, checkpoint=CKPT, model_id="m", tune=_tune(), profile_flags="")
    assert "HALOGEN_ENABLE_THINKING" not in env
    assert "HALOGEN_CTX" not in env


def test_vision_auto_without_sidecar_leaves_tower_unset(tmp_path: Any) -> None:  # Review Focus 4
    ckpt = tmp_path / "qwen38-flash-next-ht43.hgn"
    ckpt.write_bytes(b"HGN1")
    env = HalogenProvider().container_spec(_cfg(), {**_info(), "path": str(ckpt)}).env
    assert "HALOGEN_VISION_TOWER" not in env
    (tmp_path / VISION_SIDECAR).write_bytes(b"HGN1")
    env = HalogenProvider().container_spec(_cfg(), {**_info(), "path": str(ckpt)}).env
    assert env["HALOGEN_VISION_TOWER"] == "1"


def test_vision_off_never_sets_the_tower_even_with_a_sidecar(tmp_path: Any) -> None:
    ckpt = tmp_path / "qwen38-flash-next-ht43.hgn"
    ckpt.write_bytes(b"HGN1")
    (tmp_path / VISION_SIDECAR).write_bytes(b"HGN1")
    env = (
        HalogenProvider()
        .container_spec(_cfg(tune={"vision": "off"}), {**_info(), "path": str(ckpt)})
        .env
    )
    assert "HALOGEN_VISION_TOWER" not in env


def test_vision_sidecar_present(tmp_path: Any) -> None:
    ckpt = tmp_path / "qwen38-flash-next-ht43.hgn"
    assert vision_sidecar_present(str(ckpt)) is False
    (tmp_path / VISION_SIDECAR).write_bytes(b"HGN1")
    assert vision_sidecar_present(str(ckpt)) is True
    # A directory of that name is not a sidecar file.
    other = tmp_path / "sub"
    other.mkdir()
    (other / VISION_SIDECAR).mkdir()
    assert vision_sidecar_present(str(other / "m.hgn")) is False


def test_quoted_profile_value_survives() -> None:  # Review Focus 1
    ProfileCatalog().create(
        "flash-msg",
        ProfileConfig(
            flags='HALOGEN_KV_SLOTS=2 HALOGEN_THINKING_BUDGET_MESSAGE="Time is up."',
            runner="halogen",
        ),
    )
    env = HalogenProvider().container_spec(_cfg(profile="flash-msg"), _info()).env
    assert env["HALOGEN_THINKING_BUDGET_MESSAGE"] == "Time is up."
    assert env["HALOGEN_KV_SLOTS"] == "2"


def test_env_precedence_profile_then_tune_then_managed() -> None:
    env = halogen_env(
        port=8099,
        checkpoint=CKPT,
        model_id="m",
        tune=_tune(ctx_size=131072, enable_thinking=False),
        profile_flags=(
            "HALOGEN_CTX=1 HALOGEN_ENABLE_THINKING=1 HALOGEN_API_PORT=1 "
            "HALOGEN_BIND=0.0.0.0 HALOGEN_CHECKPOINT=/x HALOGEN_MODEL_ID=other HALOGEN_KV_SLOTS=3"
        ),
    )
    assert env["HALOGEN_CTX"] == "131072"
    assert env["HALOGEN_ENABLE_THINKING"] == "0"
    assert env["HALOGEN_API_PORT"] == "8099"
    assert env["HALOGEN_BIND"] == "127.0.0.1"
    assert env["HALOGEN_CHECKPOINT"] == CKPT
    assert env["HALOGEN_MODEL_ID"] == "m"
    assert env["HALOGEN_KV_SLOTS"] == "3"


def test_profile_tokens_hal0_keeps_out_of_the_container_are_dropped() -> None:
    """A profile file edited by hand past the write screen still cannot put
    downloads, auth or NPU variables in the container."""
    env = halogen_env(
        port=8099,
        checkpoint=CKPT,
        model_id="m",
        tune=_tune(),
        profile_flags=(
            "HALOGEN_DOWNLOAD=1 HALOGEN_API_KEY=secret HALOGEN_API_KEY_FILE=/k "
            "HALOGEN_NPU_X=1 HALOGEN_RECORD_Y=1 HALOGEN_CACHE_DIR=/c HALOGEN_KV_SLOTS=3"
        ),
    )
    for name in (
        "HALOGEN_DOWNLOAD",
        "HALOGEN_API_KEY",
        "HALOGEN_API_KEY_FILE",
        "HALOGEN_NPU_X",
        "HALOGEN_RECORD_Y",
        "HALOGEN_CACHE_DIR",
    ):
        assert name not in env
    assert env["HALOGEN_KV_SLOTS"] == "3"


def test_malformed_profile_flags_refuse_the_launch() -> None:
    from hal0.errors import UnprocessableEntity

    with pytest.raises(UnprocessableEntity) as exc_info:
        halogen_env(
            port=8099,
            checkpoint=CKPT,
            model_id="m",
            tune=_tune(),
            profile_flags="--ctx-size 4096",
        )
    assert exc_info.value.code == "profiles.halogen_flag_malformed"


def test_speculation_ngram_env_stays_pure_and_unset() -> None:
    """This engine has no n-gram drafter and the write seams refuse the value.
    ``halogen_env`` stays pure: a stale stored ``ngram`` maps like ``auto``
    here, and ``container_spec`` is where the ignored value is logged."""
    env = halogen_env(
        port=8099, checkpoint=CKPT, model_id="m", tune=_tune(speculation="ngram"), profile_flags=""
    )
    assert "HALOGEN_DRAFTER_DEFAULT" not in env
    assert "HALOGEN_PLD" not in env


def test_stale_ngram_speculation_is_logged_with_the_slot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="hal0.providers.halogen"):
        spec = HalogenProvider().container_spec(_cfg(tune={"speculation": "ngram"}), _info())
    assert "HALOGEN_DRAFTER_DEFAULT" not in spec.env
    messages = [r.getMessage() for r in caplog.records if "ngram" in r.getMessage()]
    assert len(messages) == 1
    assert "slot=flash" in messages[0]
    assert "halogen.speculation_ngram_ignored" in messages[0]


@pytest.mark.parametrize("speculation", ["auto", "off"])
def test_supported_speculation_values_do_not_warn(
    speculation: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="hal0.providers.halogen"):
        HalogenProvider().container_spec(_cfg(tune={"speculation": speculation}), _info())
    assert not [r for r in caplog.records if r.name == "hal0.providers.halogen"]


def test_halogen_env_is_pure() -> None:
    args: dict[str, Any] = {
        "port": 8099,
        "checkpoint": CKPT,
        "model_id": "m",
        "tune": _tune(ctx_size=262144),
        "profile_flags": "HALOGEN_KV_SLOTS=2",
    }
    assert halogen_env(**args) == halogen_env(**args)


# ── Dispatch and render ─────────────────────────────────────────────────────


def test_dispatch_family_halogen() -> None:
    assert isinstance(_spec_provider_for(_cfg()), HalogenProvider)


def test_load_refuses_a_foreign_runner_before_writing_a_unit() -> None:
    """A hand-edited ``binary`` the llama path would launch with llama-server argv.
    Nothing reaches the seam: the slot fails with the reason, not a crash loop."""
    cfg = _cfg(profile="agent", binary="halogen")
    with (
        patch("hal0.providers.container._SYSTEMCTL_SEAM") as seam,
        pytest.raises(RunnerFamilyMismatch) as exc,
    ):
        ContainerProvider().load_sync(cfg, _info())
    assert exc.value.code == "slot.runner_family_mismatch"
    assert "'agent'" in str(exc.value)
    assert exc.value.details["profiles"] == ["halogen"]
    seam.write_quadlet.assert_not_called()
    seam.systemctl.assert_not_called()


@pytest.mark.parametrize(
    "cfg",
    [
        pytest.param(_cfg(profile="halogen", binary="halogen"), id="its-own-runner"),
        pytest.param(_cfg(profile="halogen", binary="standard"), id="llama-runner-is-ignored"),
        pytest.param(_cfg(profile="agent", binary="cuda"), id="llama-family-runner"),
        pytest.param(_cfg(profile="agent"), id="no-runner"),
    ],
)
def test_runner_family_agrees(cfg: dict[str, Any]) -> None:
    assert runner_family_mismatch(cfg) is None


def test_load_still_launches_an_engine_profile_over_a_stale_runner() -> None:
    """A slot moved from Kokoro to Qwen3-TTS before the opt-out clear existed: the
    provider launches its own image and ``binary`` is inert, so a box already in
    this state keeps loading. Only a new write of the pair is refused."""
    cfg = _cfg(type="tts", device="gpu-rocm", profile="qwen3-tts", binary="kokoro")
    assert runner_family_mismatch(cfg) is not None
    provider = ContainerProvider()
    with (
        patch("hal0.providers.container.require_kfd_for_gpu_slot"),
        patch.object(provider, "_render_quadlet_text", return_value="unit"),
        patch.object(provider, "_write_and_start_unit") as start,
    ):
        provider.load_sync(cfg, _info())
    start.assert_called_once()


def test_render_contains_stop_and_restart_keys() -> None:
    unit = _render_quadlet_from_plan("flash", HalogenProvider().container_spec(_cfg(), _info()))
    assert "--stop-timeout=120" in unit
    assert "TimeoutStopSec=150" in unit
    assert "RestartPreventExitStatus=64 132 1 2" in unit.splitlines()
    assert "PublishPort=127.0.0.1:8099:8099" in unit
    assert "Environment=HALOGEN_BIND=127.0.0.1" in unit
    assert "Exec=all" in unit


def test_quoted_profile_value_renders_as_one_environment_assignment() -> None:
    """Review round 1: a value with spaces must not truncate at the first space or
    open a second assignment (a way to set HALOGEN_DOWNLOAD)."""
    ProfileCatalog().create(
        "flash-inject",
        ProfileConfig(
            flags=(
                'HALOGEN_THINKING_BUDGET_MESSAGE="Time is up." HALOGEN_X="y HALOGEN_DOWNLOAD=1"'
            ),
            runner="halogen",
        ),
    )
    plan = HalogenProvider().container_spec(_cfg(profile="flash-inject"), _info())
    unit = _render_quadlet_from_plan("flash", plan)
    assignments: list[str] = []
    for line in unit.splitlines():
        if line.startswith("Environment="):
            assignments.extend(shlex.split(line[len("Environment=") :]))
    assert "HALOGEN_THINKING_BUDGET_MESSAGE=Time is up." in assignments
    assert "HALOGEN_X=y HALOGEN_DOWNLOAD=1" in assignments
    assert not [a for a in assignments if a.startswith("HALOGEN_DOWNLOAD=")]
    assert 'Environment="HALOGEN_THINKING_BUDGET_MESSAGE=Time is up."' in unit.splitlines()


# ── ABC stubs ───────────────────────────────────────────────────────────────


def test_build_env_is_informational() -> None:
    env = HalogenProvider().build_env(_cfg(), _info())
    assert env["HAL0_SLOT"] == "flash"
    assert env["HAL0_RUNTIME"] == "container"
    assert env["HAL0_PROFILE"] == "halogen"


def test_start_cmd_is_unused() -> None:
    with pytest.raises(NotImplementedError):
        HalogenProvider().start_cmd({})


# ── Health, readiness, sentinel ─────────────────────────────────────────────


def _fake_client(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[..., Any]:
    def _make(**_kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return _make


def _patch_http(handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    return patch.object(halogen_module, "async_client", _fake_client(handler))


async def test_health_ready_when_engine_responds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        assert request.url.port == 8099
        return httpx.Response(200, json={"engine": {"responds": True}, "capability_probe": "ok"})

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {
            "ok": True,
            "status": "ready",
            "capability_probe": "ok",
        }


async def test_health_ok_rule_ignores_the_capability_probe() -> None:
    """``ok`` stays the engine-answers check; the probe rides along for
    ``wait_ready`` to judge."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"engine": {"responds": True}, "capability_probe": "failed"}
        )

    with _patch_http(handler):
        verdict = await HalogenProvider().health(8099)
    assert verdict == {"ok": True, "status": "ready", "capability_probe": "failed"}


async def test_health_loading_when_engine_not_responding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engine": {"responds": False}})

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {
            "ok": False,
            "status": "loading",
            "capability_probe": None,
        }


async def test_health_loading_when_engine_block_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with _patch_http(handler):
        assert (await HalogenProvider().health(8099))["status"] == "loading"


async def test_health_non_200_reports_http_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {
            "ok": False,
            "status": "http_503",
            "capability_probe": None,
        }


async def test_health_transport_error_is_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _patch_http(handler):
        result = await HalogenProvider().health(8099)
    assert result["ok"] is False
    assert "connection refused" in result["status"]


async def test_wait_ready_returns_once_the_engine_responds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"engine": {"responds": calls["n"] >= 3}, "capability_probe": "ok"},
        )

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
    ):
        await HalogenProvider().wait_ready(8099)
    assert calls["n"] == 3


async def test_wait_ready_waits_for_the_capability_probe() -> None:
    """The engine answering its PING is not enough: the capability probe must
    also have passed before the slot may read ready."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        probe = "ok" if calls["n"] >= 4 else "pending"
        return httpx.Response(200, json={"engine": {"responds": True}, "capability_probe": probe})

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
    ):
        await HalogenProvider().wait_ready(8099)
    assert calls["n"] == 4


async def test_wait_ready_times_out_on_a_failed_capability_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"engine": {"responds": True}, "capability_probe": "failed"}
        )

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.01),
        patch.object(HalogenProvider, "READY_TIMEOUT_S", 0.05),
        pytest.raises(TimeoutError) as exc_info,
    ):
        await HalogenProvider().wait_ready(8099)
    assert "capability_probe" in str(exc_info.value)
    assert "failed" in str(exc_info.value)


async def test_wait_ready_times_out_when_the_capability_probe_is_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engine": {"responds": True}})

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.01),
        patch.object(HalogenProvider, "READY_TIMEOUT_S", 0.05),
        pytest.raises(TimeoutError),
    ):
        await HalogenProvider().wait_ready(8099)


async def test_wait_ready_times_out() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engine": {"responds": False}})

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.01),
        patch.object(HalogenProvider, "READY_TIMEOUT_S", 0.05),
        pytest.raises(TimeoutError),
    ):
        await HalogenProvider().wait_ready(8099)


# ── capability grace (a failed probe does not strand a responding engine) ───
#
# The front end re-probes on the next real request, and a slot that is not ready
# gets none, so hal0 stops waiting after CAPABILITY_GRACE_S and lets the sentinel
# be that request. Driven by a clock the handler advances, so these prove the
# budgets themselves rather than a scaled-down copy of them.


def _clocked_health(
    bodies: Callable[[int], dict[str, Any]], *, step_s: float
) -> tuple[Callable[[httpx.Request], httpx.Response], Callable[[], float], dict[str, int]]:
    """A ``/health`` handler that advances ``clock`` by ``step_s`` per call."""
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        return httpx.Response(200, json=bodies(state["n"]))

    def clock() -> float:
        return state["n"] * step_s

    return handler, clock, state


def _engine_up(probe: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"engine": {"responds": True}}
    if probe is not None:
        body["capability_probe"] = probe
    return body


async def test_wait_ready_returns_once_a_failed_probe_holds_for_the_grace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, clock, state = _clocked_health(lambda n: _engine_up("failed"), step_s=20.0)
    assert HalogenProvider.CAPABILITY_GRACE_S == 60.0
    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
        patch.object(asyncio.get_running_loop(), "time", clock),
        caplog.at_level("WARNING", logger="hal0.providers.halogen"),
    ):
        await HalogenProvider().wait_ready(8099)
    # First seen on call 1 (t=20); 60 s have held at call 4 (t=80).
    assert state["n"] == 4
    warning = "\n".join(r.getMessage() for r in caplog.records)
    assert "capability_probe" in warning
    assert "8099" in warning


async def test_wait_ready_a_failed_probe_that_turns_ok_returns_at_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, clock, state = _clocked_health(
        lambda n: _engine_up("ok" if n >= 2 else "failed"), step_s=1.0
    )
    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
        patch.object(asyncio.get_running_loop(), "time", clock),
        caplog.at_level("WARNING", logger="hal0.providers.halogen"),
    ):
        await HalogenProvider().wait_ready(8099)
    assert state["n"] == 2
    assert caplog.records == []


async def test_wait_ready_the_grace_restarts_when_the_failure_does_not_hold() -> None:
    """The window is a held failure, not a total: the engine going quiet in the
    middle starts it again."""

    def body(n: int) -> dict[str, Any]:
        return {"engine": {"responds": False}} if n == 3 else _engine_up("failed")

    handler, clock, state = _clocked_health(body, step_s=20.0)
    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
        patch.object(asyncio.get_running_loop(), "time", clock),
    ):
        await HalogenProvider().wait_ready(8099)
    # Failed at 1 and 2, broken at 3, then first seen again at 4; 60 s held at 7.
    assert state["n"] == 7


@pytest.mark.parametrize(
    ("body", "named"),
    [
        pytest.param(_engine_up(), "capability_probe=None", id="missing-field"),
        pytest.param(_engine_up("pending"), "capability_probe='pending'", id="other-value"),
        pytest.param(
            {"engine": {"responds": False}, "capability_probe": "failed"},
            "capability_probe='failed'",
            id="failed-but-engine-silent",
        ),
    ],
)
async def test_wait_ready_anything_else_rides_out_the_full_budget(
    body: dict[str, Any], named: str
) -> None:
    handler, clock, state = _clocked_health(lambda n: body, step_s=50.0)
    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
        patch.object(asyncio.get_running_loop(), "time", clock),
        pytest.raises(TimeoutError) as exc_info,
    ):
        await HalogenProvider().wait_ready(8099)
    assert clock() >= HalogenProvider.READY_TIMEOUT_S
    assert named in str(exc_info.value)


# ── liveness (the fail-watcher's probe, not readiness) ──────────────────────


async def test_liveness_200_is_alive_without_judging_the_engine() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json={"engine": {"responds": False}})

    with _patch_http(handler):
        verdict = await HalogenProvider().liveness(8099)
    assert verdict["ok"] is True
    assert verdict["status"] != "stalled"


async def test_liveness_read_timeout_is_a_stall_not_a_death() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    with _patch_http(handler):
        assert await HalogenProvider().liveness(8099) == {"ok": True, "status": "stalled"}


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.ConnectError("connection refused"), id="refused"),
        pytest.param(httpx.ReadError("connection reset"), id="reset"),
        pytest.param(httpx.RemoteProtocolError("server disconnected"), id="closed"),
        pytest.param(httpx.ConnectTimeout("no route"), id="connect-timeout"),
    ],
)
async def test_liveness_a_broken_connection_is_not_ok(error: httpx.HTTPError) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with _patch_http(handler):
        verdict = await HalogenProvider().liveness(8099)
    assert verdict["ok"] is False
    assert verdict["status"] != "stalled"


async def test_liveness_other_status_is_not_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    with _patch_http(handler):
        assert await HalogenProvider().liveness(8099) == {"ok": False, "status": "http_503"}


async def test_liveness_read_budget_is_the_slow_ping_figure() -> None:
    seen: dict[str, Any] = {}
    real = _fake_client(lambda request: httpx.Response(200, json={}))

    def recording(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(**kwargs)

    with patch.object(halogen_module, "async_client", recording):
        await HalogenProvider().liveness(8099)
    assert seen["timeout"].read == 35.0


async def test_container_health_sends_a_halogen_slot_to_liveness() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    with _patch_http(handler):
        verdict = await ContainerProvider().health(8099, _cfg())
    assert verdict == {"ok": True, "status": "stalled"}


async def test_a_non_halogen_slot_keeps_the_generic_three_second_probe() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer")

    real = _fake_client(handler)

    def recording(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return real(**kwargs)

    with patch("hal0.providers.container.async_client", recording):
        verdict = await ContainerProvider().health(8081, _cfg(profile="agent"))
    assert seen["timeout"] == 3.0
    assert verdict["ok"] is False


def test_readiness_budget_is_declared_only_for_halogen() -> None:
    assert readiness_probe_budget_s(_cfg()) == HalogenProvider.readiness_budget_s
    assert HalogenProvider.readiness_budget_s < HalogenProvider.LIVENESS_READ_TIMEOUT_S
    assert readiness_probe_budget_s(_cfg(profile="agent")) is None


async def test_wait_ready_has_a_budget_inside_the_warming_watchdog() -> None:
    # 600 s sits under the WARMING watchdog's 900 s; never the generic 180 s.
    assert 180.0 < HalogenProvider.READY_TIMEOUT_S < 900.0
    assert asyncio.iscoroutinefunction(HalogenProvider.wait_ready)


async def test_verify_inference_posts_the_sentinel_and_passes_on_paris() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": " Paris."}}]},
        )

    with _patch_http(handler):
        verdict = await HalogenProvider().verify_inference(8099)
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"] == {
        "messages": [{"role": "user", "content": output_sanity.SANITY_PROMPT}],
        "max_tokens": 32,
        "temperature": 0,
        "enable_thinking": False,
        "stream": False,
    }
    assert verdict["ok"] is True
    assert verdict["status"] == "ok"
    assert "Paris" in verdict["sample"]


async def test_verify_inference_fails_on_garbage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": ")))))))"}}]})

    with _patch_http(handler):
        verdict = await HalogenProvider().verify_inference(8099)
    assert verdict["ok"] is False
    assert verdict["status"] == "incoherent_output"


async def test_verify_inference_empty_answer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]})

    with _patch_http(handler):
        verdict = await HalogenProvider().verify_inference(8099)
    assert verdict["ok"] is False
    assert verdict["status"] == "empty_output"


async def test_verify_inference_transport_error_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with _patch_http(handler):
        verdict = await HalogenProvider().verify_inference(8099)
    assert verdict["ok"] is False
    assert verdict["status"]


async def test_verify_inference_http_error_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with _patch_http(handler):
        verdict = await HalogenProvider().verify_inference(8099)
    assert verdict["ok"] is False
    assert verdict["status"]


async def test_infer_passthrough_posts_chat_completions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": []})

    with _patch_http(handler):
        assert await HalogenProvider().infer(8099, {"messages": []}) == {"choices": []}


async def test_infer_wraps_http_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    with _patch_http(handler), pytest.raises(Hal0Error) as exc_info:
        await HalogenProvider().infer(8099, {"messages": []})
    assert exc_info.value.code == "dispatch.upstream_failed"
