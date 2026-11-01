"""halogen (Flash-Next) container spec, env mapping, readiness and sentinel.

The provider turns the slot's effective tune plus the profile's
``HALOGEN_NAME=value`` flag tokens into one launch plan. Everything the engine
reads is an environment variable, so the env mapping carries most of the
assertions: hal0-managed facts always set and last, tune-owned variables from
``effective_tune``, profile tokens first and shadowed by both.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from hal0.config.schema import ProfileConfig
from hal0.errors import Hal0Error
from hal0.profiles import ProfileCatalog
from hal0.providers import halogen as halogen_module
from hal0.providers.container import _render_quadlet_from_plan, _spec_provider_for
from hal0.providers.halogen import (
    VISION_SIDECAR,
    HalogenProvider,
    halogen_env,
    vision_sidecar_present,
)
from hal0.slots import output_sanity
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
    assert spec.restart_prevent_exit_status == (1,)
    assert spec.port == 8099
    assert spec.network_mode == ""
    assert spec.devices == _FAKE_DEVICES
    assert spec.group_add == ["993", "44"]
    assert [m.target for m in spec.mounts] == ["/mnt/ai-models", "/cache"]
    assert spec.mounts[0].read_only


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


def test_speculation_ngram_maps_like_auto() -> None:
    """This engine has no n-gram drafter; the write seams refuse the value, and a
    stale stored one launches the engine default instead of a made-up variable."""
    env = halogen_env(
        port=8099, checkpoint=CKPT, model_id="m", tune=_tune(speculation="ngram"), profile_flags=""
    )
    assert "HALOGEN_DRAFTER_DEFAULT" not in env
    assert "HALOGEN_PLD" not in env


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


def test_render_contains_stop_and_restart_keys() -> None:
    unit = _render_quadlet_from_plan("flash", HalogenProvider().container_spec(_cfg(), _info()))
    assert "--stop-timeout=120" in unit
    assert "TimeoutStopSec=150" in unit
    assert "RestartPreventExitStatus=64 132 1" in unit
    assert "PublishPort=127.0.0.1:8099:8099" in unit
    assert "Environment=HALOGEN_BIND=127.0.0.1" in unit
    assert "Exec=all" in unit


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
        return httpx.Response(200, json={"engine": {"responds": True}})

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {"ok": True, "status": "ready"}


async def test_health_loading_when_engine_not_responding() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"engine": {"responds": False}})

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {"ok": False, "status": "loading"}


async def test_health_loading_when_engine_block_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with _patch_http(handler):
        assert (await HalogenProvider().health(8099))["status"] == "loading"


async def test_health_non_200_reports_http_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    with _patch_http(handler):
        assert await HalogenProvider().health(8099) == {"ok": False, "status": "http_503"}


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
        return httpx.Response(200, json={"engine": {"responds": calls["n"] >= 3}})

    with (
        _patch_http(handler),
        patch.object(HalogenProvider, "READY_POLL_S", 0.0),
    ):
        await HalogenProvider().wait_ready(8099)
    assert calls["n"] == 3


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
