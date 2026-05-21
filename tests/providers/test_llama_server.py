"""Unit tests for LlamaServerProvider.

Covers build_env, start_cmd, container_spec, image_ref, and health
(mocked httpx). Health tests explicitly exercise the Tier 1 fix:
non-empty /v1/models PLUS sentinel /v1/chat/completions both required.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from hal0.providers.llama_server import (
    _HAL0_TOOLBOX_IMAGES,
    LlamaServerProvider,
    ProviderInferError,
)

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def provider() -> LlamaServerProvider:
    return LlamaServerProvider()


@pytest.fixture
def slot_cfg() -> dict[str, Any]:
    return {
        "port": 8081,
        "backend": "vulkan",
        "ctx_size": 8192,
        "threads": 8,
        "parallel": 4,
        "_paths": {
            "models_base": "/var/lib/hal0/models",
            "llama_vulkan": "/opt/llama-vulkan/llama-server",
            "llama_vulkan_lib": "/opt/llama-vulkan/lib",
        },
    }


@pytest.fixture
def model_info() -> dict[str, Any]:
    return {
        "path": "/var/lib/hal0/models/qwen3-4b.gguf",
        "max_context": 8192,
        "gpu_layers": 33,
    }


# ─── build_env ────────────────────────────────────────────────────────────────


def test_build_env_returns_hal0_namespaced_vars(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    # Tier 1 / rebrand: vars are HAL0_*, not HALOAI_*.
    assert all(k.startswith("HAL0_") for k in env), env.keys()
    for key in ("HAL0_MODEL", "HAL0_PORT", "HAL0_CTX", "HAL0_BACKEND", "HAL0_BINARY"):
        assert key in env


def test_build_env_uses_slot_backend_over_model_preferred(
    provider: LlamaServerProvider, model_info: dict[str, Any]
) -> None:
    slot_cfg = {"port": 8081, "backend": "rocm", "_paths": {}}
    model_info["preferred_backend"] = "vulkan"
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_BACKEND"] == "rocm"
    # ROCm gets the rocm binary path by default.
    assert "rocm" in env["HAL0_BINARY"]


def test_build_env_clamps_ctx_to_model_max(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    slot_cfg["ctx_size"] = 999999
    model_info["max_context"] = 4096
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_CTX"] == "4096"


def test_build_env_handles_legacy_nested_keys(
    provider: LlamaServerProvider, model_info: dict[str, Any]
) -> None:
    """Legacy slot TOML with nested [slot]/[defaults] still loads."""
    slot_cfg = {
        "slot": {"port": 9090, "backend": "vulkan"},
        "defaults": {"context_size": 2048, "threads": 6},
        "_paths": {},
    }
    env = provider.build_env(slot_cfg, model_info)
    assert env["HAL0_PORT"] == "9090"
    assert env["HAL0_BACKEND"] == "vulkan"
    assert env["HAL0_THREADS"] == "6"


def test_build_env_emits_mmproj_for_vision_models(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    model_info["mmproj"] = "/var/lib/hal0/models/qwen3-vl-mmproj.gguf"
    env = provider.build_env(slot_cfg, model_info)
    assert "--mmproj" in env["HAL0_EXTRA_ARGS"]
    assert model_info["mmproj"] in env["HAL0_EXTRA_ARGS"]


def test_build_env_emits_embedding_flag_when_model_is_embedding(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    model_info["embedding"] = True
    env = provider.build_env(slot_cfg, model_info)
    assert "--embedding" in env["HAL0_EXTRA_ARGS"]


def test_build_env_emits_embedding_flag_when_model_has_embed_capability(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    model_info.pop("embedding", None)
    model_info["capabilities"] = ["embed"]
    env = provider.build_env(slot_cfg, model_info)
    assert "--embedding" in env["HAL0_EXTRA_ARGS"]


# ─── A3 flag-merge wiring (model.defaults.extra_args ⊕ slot.server.extra_args)
#
# These exercise the launcher's arg-build site that consumes the new
# `merge_flags` util from hal0.slots.flag_merge.  The merged string ends
# up appended (shlex-split) to HAL0_EXTRA_ARGS, so we assert on that
# env var rather than on start_cmd output.


def test_build_env_no_extra_args_when_both_empty(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """No model defaults + no slot override → no merged flags injected."""
    env = provider.build_env(slot_cfg, model_info)
    # Must not contain a merged-flag we'd expect from either source.
    assert "--lora" not in env["HAL0_EXTRA_ARGS"]
    assert "--rope-freq-base" not in env["HAL0_EXTRA_ARGS"]


def test_build_env_picks_up_model_defaults_extra_args(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """model_info['defaults']['extra_args'] only → merged into HAL0_EXTRA_ARGS."""
    model_info["defaults"] = {"extra_args": "--rope-freq-base 10000"}
    env = provider.build_env(slot_cfg, model_info)
    assert "--rope-freq-base" in env["HAL0_EXTRA_ARGS"]
    assert "10000" in env["HAL0_EXTRA_ARGS"]


def test_build_env_picks_up_slot_server_extra_args(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """slot_cfg['server']['extra_args'] only → merged into HAL0_EXTRA_ARGS."""
    slot_cfg["server"] = {"extra_args": "--lora /tmp/lora.gguf"}
    env = provider.build_env(slot_cfg, model_info)
    assert "--lora" in env["HAL0_EXTRA_ARGS"]
    assert "/tmp/lora.gguf" in env["HAL0_EXTRA_ARGS"]


def test_build_env_slot_wins_over_model_defaults_on_collision(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """When both sides set the same flag, the slot's value wins."""
    model_info["defaults"] = {"extra_args": "--rope-freq-base 10000"}
    slot_cfg["server"] = {"extra_args": "--rope-freq-base 500000"}
    env = provider.build_env(slot_cfg, model_info)
    assert "500000" in env["HAL0_EXTRA_ARGS"]
    assert "10000" not in env["HAL0_EXTRA_ARGS"]


def test_build_env_merges_both_when_no_collision(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """Non-overlapping flags from model + slot both end up in HAL0_EXTRA_ARGS."""
    model_info["defaults"] = {"extra_args": "--rope-freq-base 10000"}
    slot_cfg["server"] = {"extra_args": "--lora /tmp/lora.gguf"}
    env = provider.build_env(slot_cfg, model_info)
    assert "--rope-freq-base" in env["HAL0_EXTRA_ARGS"]
    assert "--lora" in env["HAL0_EXTRA_ARGS"]


# ─── start_cmd ────────────────────────────────────────────────────────────────


def test_start_cmd_includes_required_flags(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    env = provider.build_env(slot_cfg, model_info)
    cmd = provider.start_cmd(env)
    # First element is the binary, then flag/value pairs.
    assert cmd[0] == env["HAL0_BINARY"]
    assert "--model" in cmd
    assert "--port" in cmd
    assert "--ctx-size" in cmd
    assert "--threads" in cmd
    assert "-ngl" in cmd
    assert "--host" in cmd
    assert "0.0.0.0" in cmd


def test_start_cmd_appends_extra_args_when_present(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    model_info["mmproj"] = "/x/mmproj.gguf"
    env = provider.build_env(slot_cfg, model_info)
    cmd = provider.start_cmd(env)
    assert "--mmproj" in cmd
    assert "/x/mmproj.gguf" in cmd


# ─── image_ref ────────────────────────────────────────────────────────────────


def test_image_ref_default_vulkan(provider: LlamaServerProvider) -> None:
    assert provider.image_ref({}) == _HAL0_TOOLBOX_IMAGES["vulkan"]


def test_image_ref_rocm(provider: LlamaServerProvider) -> None:
    assert provider.image_ref({"backend": "rocm"}) == _HAL0_TOOLBOX_IMAGES["rocm"]


def test_image_ref_cpu_falls_through_to_vulkan(provider: LlamaServerProvider) -> None:
    assert provider.image_ref({"backend": "cpu"}) == _HAL0_TOOLBOX_IMAGES["vulkan"]


def test_image_ref_rejects_unknown_backend(provider: LlamaServerProvider) -> None:
    with pytest.raises(ValueError, match="Unknown llama-server backend"):
        provider.image_ref({"backend": "tpu"})


def test_image_ref_uses_hal0ai_namespace(provider: LlamaServerProvider) -> None:
    """PLAN.md §12: toolbox images live under ghcr.io/hal0ai/."""
    for backend in ("vulkan", "rocm"):
        ref = provider.image_ref({"backend": backend})
        assert ref.startswith("ghcr.io/hal0ai/hal0-toolbox-"), ref


def test_image_ref_slot_cfg_override_wins(provider: LlamaServerProvider) -> None:
    """slot_cfg["image"] overrides the default map (local-build path)."""
    ref = provider.image_ref({"backend": "vulkan", "image": "hal0-toolbox-vulkan:dev"})
    assert ref == "hal0-toolbox-vulkan:dev"


def test_image_ref_env_var_override(
    provider: LlamaServerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HAL0_TOOLBOX_IMAGE_<BACKEND> env var overrides the default map."""
    monkeypatch.setenv(
        "HAL0_TOOLBOX_IMAGE_VULKAN", "ghcr.io/example/hal0-toolbox-vulkan@sha256:abc"
    )
    ref = provider.image_ref({"backend": "vulkan"})
    assert ref == "ghcr.io/example/hal0-toolbox-vulkan@sha256:abc"


def test_image_ref_slot_cfg_wins_over_env(
    provider: LlamaServerProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-slot image override beats the env-var override."""
    monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_VULKAN", "ghcr.io/example/from-env:v1")
    ref = provider.image_ref({"backend": "vulkan", "image": "from-slot:dev"})
    assert ref == "from-slot:dev"


# ─── container_spec ───────────────────────────────────────────────────────────


def test_container_spec_returns_frozen_dataclass(
    monkeypatch: pytest.MonkeyPatch,
    provider: LlamaServerProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    # Pretend /dev/kfd + /dev/dri exist so the host-aware device filter
    # in container_spec keeps them in the rendered spec.
    monkeypatch.setattr("hal0.providers.llama_server.Path.exists", lambda self: True)
    spec = provider.container_spec(slot_cfg, model_info)
    assert spec.image == _HAL0_TOOLBOX_IMAGES["vulkan"]
    assert spec.port == 8081
    assert spec.network_mode == "host"
    # /dev/kfd + /dev/dri for both Vulkan and ROCm (cheap, no-op on Vulkan).
    assert "/dev/kfd" in spec.devices
    assert "/dev/dri" in spec.devices
    # The toolbox image ENTRYPOINT=llama-server, so command[0] is a flag.
    assert spec.command[0].startswith("--")


def test_container_spec_filters_missing_devices(
    monkeypatch: pytest.MonkeyPatch,
    provider: LlamaServerProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
) -> None:
    """A CPU-only / virtio-gpu host without /dev/kfd gets an empty devices list."""
    monkeypatch.setattr("hal0.providers.llama_server.Path.exists", lambda self: False)
    spec = provider.container_spec(slot_cfg, model_info)
    assert spec.devices == []


def test_container_spec_mounts_models_base(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    spec = provider.container_spec(slot_cfg, model_info)
    host_paths = [m[0] for m in spec.mounts]
    assert "/var/lib/hal0/models" in host_paths


def test_container_spec_group_add_uses_numeric_gids(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any]
) -> None:
    """group_add must be numeric strings, not group names.

    The toolbox image inherits ubuntu:24.04's /etc/group, which doesn't
    define ``render``/``video``.  Passing names there fails fast with
    "unable to find group render".  Numeric GIDs route around it.
    """
    spec = provider.container_spec(slot_cfg, model_info)
    assert spec.group_add, "expected at least one GID in group_add"
    for entry in spec.group_add:
        assert entry.isdigit(), f"group_add entry {entry!r} must be a numeric GID, not a name"


# ─── render_systemd_override ──────────────────────────────────────────────────


def test_render_systemd_override_emits_full_docker_line(
    monkeypatch: pytest.MonkeyPatch,
    provider: LlamaServerProvider,
    slot_cfg: dict[str, Any],
    model_info: dict[str, Any],
    tmp_path,
) -> None:
    """The drop-in carries every ContainerSpec field that docker-run needs.

    Failure mode this guards: a rendered override that's missing
    ``--device``, ``--group-add``, or the trailing command args ships a
    container with no GPU access (silent llvmpipe fallback) or one that
    just exits with ``--help``.
    """
    env_file = tmp_path / "env"
    # Pretend /dev/kfd + /dev/dri exist on the host so the device filter
    # in container_spec keeps them in the rendered docker line.
    import hal0.providers.llama_server as ls_mod

    monkeypatch.setattr(ls_mod.Path, "exists", lambda self: True)
    out = provider.render_systemd_override(
        "primary",
        slot_cfg,
        model_info,
        env_file_path=env_file,
    )
    # systemd plumbing
    assert "ExecStart=" in out
    assert "ExecStop=" in out
    assert f"EnvironmentFile={env_file}" in out
    assert "SyslogIdentifier=hal0-slot-primary" in out
    # docker run plumbing
    assert "/usr/bin/docker run --rm" in out
    assert "--name hal0-slot-primary" in out
    assert f"--env-file {env_file}" in out
    assert "--network host" in out
    # iGPU passthrough — the whole point of switching to ContainerSpec.
    assert "--device /dev/kfd" in out
    assert "--device /dev/dri" in out
    assert "--group-add" in out
    assert "--security-opt apparmor=unconfined" in out
    # bind-mount for the model file
    assert "/var/lib/hal0/models:/var/lib/hal0/models" in out
    # image + ENTRYPOINT args (llama-server flags)
    assert "ghcr.io/hal0ai/hal0-toolbox-vulkan:v1" in out
    assert "--model" in out
    assert "--port 8081" in out or "--port" in out
    assert "-ngl" in out


def test_render_systemd_override_quotes_var_refs_literally(
    provider: LlamaServerProvider, slot_cfg: dict[str, Any], model_info: dict[str, Any], tmp_path
) -> None:
    """``${VAR}`` references must reach systemd unquoted for expansion.

    If a ``${HAL0_MODEL_PATH}`` literal were shell-quoted, systemd would
    not expand it and the container would receive the literal string
    ``${HAL0_MODEL_PATH}`` as the model path argument.
    """
    # Spoof a command arg that references a systemd env var.
    from dataclasses import replace

    spec = provider.container_spec(slot_cfg, model_info)
    spec_with_var = replace(spec, command=["--model", "${HAL0_MODEL_PATH}"])

    # Bypass the public API and call the renderer directly to feed our
    # mutated spec in.
    from hal0.slots.unit_template import _render_from_spec

    env_file = tmp_path / "env"
    out = _render_from_spec("primary", spec_with_var, "llama-server", env_file_path=env_file)
    assert "${HAL0_MODEL_PATH}" in out
    # Must NOT be wrapped in single quotes (which suppresses expansion).
    assert "'${HAL0_MODEL_PATH}'" not in out


# ─── health ───────────────────────────────────────────────────────────────────


def _mock_async_response(
    *, status_code: int = 200, json_payload: Any = None, text: str = ""
) -> MagicMock:
    """Construct a MagicMock that mimics httpx.Response.

    NOTE: httpx.Response.raise_for_status() is SYNC, not async — using
    AsyncMock(spec=httpx.Response) wraps it as a coroutine which never
    raises. MagicMock with sync side_effect is the right shape.
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json = lambda: json_payload
    resp.text = text
    if status_code < 400:
        resp.raise_for_status = MagicMock(return_value=None)
    else:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                f"http {status_code}", request=MagicMock(), response=resp
            )
        )
    return resp


@pytest.mark.asyncio
async def test_health_ok_requires_both_models_and_sentinel(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: /v1/models non-empty AND /v1/chat/completions both required."""
    models_payload = {"data": [{"id": "qwen3-4b"}]}
    chat_payload = {"choices": [{"message": {"content": "x"}}]}

    async def _fake_get(url: str) -> httpx.Response:
        assert url.endswith("/v1/models")
        return _mock_async_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        assert url.endswith("/v1/chat/completions")
        # TIER1: assert the sentinel body shape.
        assert json["max_tokens"] == 1
        assert json["model"] == "qwen3-4b"
        return _mock_async_response(status_code=200, json_payload=chat_payload)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["model"] == "qwen3-4b"


@pytest.mark.asyncio
async def test_health_empty_models_endpoint_is_not_ready(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: empty /v1/models must report not-ready (was the haloai bug)."""

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload={"data": []})

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        raise AssertionError("must not call /v1/chat/completions when models empty")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is False
    assert result["status"] == "models_endpoint_empty"


@pytest.mark.asyncio
async def test_health_failed_sentinel_completion_is_not_ready(
    provider: LlamaServerProvider,
) -> None:
    """TIER1: sentinel completion failing → not-ready."""
    models_payload = {"data": [{"id": "qwen3-4b"}]}

    async def _fake_get(url: str) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload=models_payload)

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=500, text="model not loaded")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        client.post = _fake_post
        result = await provider.health(8081)

    assert result["ok"] is False
    assert "sentinel_completion_http_500" in result["status"]


@pytest.mark.asyncio
async def test_health_transport_error_surfaces_typed_status(
    provider: LlamaServerProvider,
) -> None:
    async def _fake_get(url: str) -> httpx.Response:
        raise httpx.ConnectError("ECONNREFUSED")

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.get = _fake_get
        result = await provider.health(8081)

    assert result["ok"] is False
    assert result["status"] == "http_error"
    assert "ECONNREFUSED" in result["detail"]


# ─── infer ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_infer_returns_upstream_json(provider: LlamaServerProvider) -> None:
    expected = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}

    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=200, json_payload=expected)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        out = await provider.infer(8081, {"model": "x", "messages": []})

    assert out == expected


@pytest.mark.asyncio
async def test_infer_raises_typed_error_on_5xx(provider: LlamaServerProvider) -> None:
    async def _fake_post(url: str, json: dict[str, Any]) -> httpx.Response:
        return _mock_async_response(status_code=503)

    with patch("hal0.providers.llama_server.httpx.AsyncClient") as MockClient:
        client = MockClient.return_value.__aenter__.return_value
        client.post = _fake_post
        with pytest.raises(ProviderInferError) as exc:
            await provider.infer(8081, {})
    assert exc.value.code == "dispatch.upstream_failed"


# ─── parse_metrics ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_metrics_whitelists_counters(
    provider: LlamaServerProvider,
) -> None:
    raw = (
        "# HELP llamacpp:n_decode_total decoded tokens\n"
        "# TYPE llamacpp:n_decode_total counter\n"
        "llamacpp:n_decode_total 1234\n"
        "llamacpp:kv_cache_usage_ratio 0.42\n"
        "llamacpp:unknown_metric 7\n"
    )
    out = await provider.parse_metrics(raw)
    assert out["decode_total"] == 1234
    assert out["kv_cache_usage"] == pytest.approx(0.42)
    assert "unknown_metric" not in out
