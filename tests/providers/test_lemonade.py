"""Unit tests for ``hal0.providers.lemonade.LemonadeProvider``.

PR-8 capability dispatch wiring. Covers:

  * ``device_to_backend`` mapping (plan §4.1 + ADR-0008 §6)
  * ``LemonadeProvider.load`` body construction → ``LemonadeClient.load``
  * ``LemonadeProvider.unload`` idempotence + noop on modelless slot
  * ``LemonadeProvider.status`` derivation from ``/v1/health.loaded[]``
  * ``LemonadeProvider.health`` envelope shape (ok=True/False)
  * ABC stub behaviour (``container_spec`` /
    ``render_systemd_override`` raise; ``build_env`` /
    ``image_ref`` / ``start_cmd`` return informational data)
  * ``lemonade_active`` env-var gating

Mocks ``LemonadeClient`` via ``httpx.MockTransport`` — same pattern as
``tests/lemonade/test_client.py``. We exercise the full request path
(serialisation + parsing) so a bug in either layer surfaces here too.
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import LemonadeHTTPError, LemonadeLoadError
from hal0.providers.lemonade import (
    LemonadeProvider,
    device_to_backend,
    lemonade_active,
)


def _mock_client(handler) -> LemonadeClient:
    """Build a LemonadeClient backed by an httpx MockTransport."""
    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    return LemonadeClient(http_client=transport)


def _slot_cfg(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "primary",
        "port": 8081,
        "device": "gpu-rocm",
        "provider": "lemonade",
        "model": {"default": "hermes-4-14b", "context_size": 8192},
    }
    base.update(overrides)
    return base


# ── device_to_backend ────────────────────────────────────────────────


def test_device_to_backend_rocm() -> None:
    assert device_to_backend("gpu-rocm") == (None, "rocm")


def test_device_to_backend_vulkan() -> None:
    assert device_to_backend("gpu-vulkan") == (None, "vulkan")


def test_device_to_backend_cpu() -> None:
    assert device_to_backend("cpu") == (None, "cpu")


def test_device_to_backend_npu() -> None:
    # NPU uses the FLM recipe; no llamacpp_backend.
    assert device_to_backend("npu") == ("flm", None)


def test_device_to_backend_empty_returns_double_none() -> None:
    assert device_to_backend("") == (None, None)
    assert device_to_backend(None) == (None, None)


def test_device_to_backend_unknown_falls_back_to_double_none() -> None:
    # Unknown devices return ``(None, None)`` so Lemonade picks its
    # defaults rather than us trying to invent a backend tag.
    assert device_to_backend("rocm-xtreme-edition") == (None, None)


def test_device_to_backend_is_case_insensitive() -> None:
    assert device_to_backend("GPU-ROCM") == (None, "rocm")
    assert device_to_backend("  npu  ") == ("flm", None)


# ── lemonade_active ──────────────────────────────────────────────────


def test_lemonade_active_true_when_env_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_BACKEND", "lemonade")
    assert lemonade_active() is True


def test_lemonade_active_false_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HAL0_BACKEND", raising=False)
    assert lemonade_active() is False


def test_lemonade_active_tolerates_whitespace_and_case(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HAL0_BACKEND", "  Lemonade ")
    assert lemonade_active() is True


# ── load() — request body construction ───────────────────────────────


@pytest.mark.asyncio
async def test_load_posts_model_name_with_rocm_backend() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == "/v1/load"
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.load(_slot_cfg(device="gpu-rocm"))
    assert result == {"status": "loaded"}
    assert captured["body"] == {
        "model_name": "hermes-4-14b",
        "ctx_size": 8192,
        "llamacpp_backend": "rocm",
    }


@pytest.mark.asyncio
async def test_load_uses_flm_recipe_for_npu_and_omits_llamacpp_backend() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    provider = LemonadeProvider(client=_mock_client(h))
    await provider.load(
        _slot_cfg(
            device="npu",
            model={"default": "gemma3:1b", "context_size": 4096},
        )
    )
    # NPU → recipe="flm", no llamacpp_backend in body.
    assert captured["body"]["recipe"] == "flm"
    assert "llamacpp_backend" not in captured["body"]


@pytest.mark.asyncio
async def test_load_omits_ctx_size_when_unset() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    provider = LemonadeProvider(client=_mock_client(h))
    await provider.load(_slot_cfg(model={"default": "hermes-4-14b"}))
    assert "ctx_size" not in captured["body"]


@pytest.mark.asyncio
async def test_load_serialises_server_extra_args_as_string() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "loaded"})

    provider = LemonadeProvider(client=_mock_client(h))
    cfg = _slot_cfg(server={"extra_args": "--parallel 1 --threads 8"})
    await provider.load(cfg)
    # Wire format is a single space-separated string (memory
    # ``hal0_lemonade_v1_load_schema`` — nlohmann::json raises on lists).
    assert captured["body"]["llamacpp_args"] == "--parallel 1 --threads 8"


@pytest.mark.asyncio
async def test_load_raises_value_error_when_no_model_default() -> None:
    def h(_: httpx.Request) -> httpx.Response:  # pragma: no cover — not hit
        raise AssertionError("client.load should never be called")

    provider = LemonadeProvider(client=_mock_client(h))
    with pytest.raises(ValueError) as exc:
        await provider.load(_slot_cfg(model={"default": ""}))
    assert "model_name" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_load_propagates_lemonade_load_error() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "evict-all triggered"})

    provider = LemonadeProvider(client=_mock_client(h))
    with pytest.raises(LemonadeLoadError) as exc:
        await provider.load(_slot_cfg())
    assert exc.value.status_code == 500


# ── unload() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unload_posts_model_name_to_v1_unload() -> None:
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/unload"
        captured["body"] = _json.loads(req.content.decode())
        return httpx.Response(200, json={"status": "unloaded"})

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.unload(_slot_cfg())
    assert result == {"status": "unloaded"}
    assert captured["body"] == {"model_name": "hermes-4-14b"}


@pytest.mark.asyncio
async def test_unload_is_noop_when_no_model_default() -> None:
    def h(_: httpx.Request) -> httpx.Response:  # pragma: no cover — not hit
        raise AssertionError("client.unload should never be called")

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.unload(_slot_cfg(model={"default": ""}))
    assert result == {"ok": True, "noop": "no model to unload"}


# ── status() ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_returns_loaded_true_when_model_in_health_loaded_list() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/v1/health"
        return httpx.Response(
            200,
            json={
                "loaded": [
                    {
                        "model_name": "hermes-4-14b",
                        "backend_url": "http://127.0.0.1:14000",
                        "last_use": 1715000000.0,
                    },
                    {"model_name": "other-model", "backend_url": "http://127.0.0.1:14001"},
                ]
            },
        )

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg())
    assert snap["loaded"] is True
    assert snap["model_name"] == "hermes-4-14b"
    assert snap["backend_url"] == "http://127.0.0.1:14000"
    assert snap["last_use"] == 1715000000.0


@pytest.mark.asyncio
async def test_status_accepts_all_models_loaded_field_name() -> None:
    """Lemonade has used two field names across versions; accept both."""

    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "all_models_loaded": [
                    {"model_name": "hermes-4-14b", "backend_url": "http://127.0.0.1:14000"}
                ]
            },
        )

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg())
    assert snap["loaded"] is True


@pytest.mark.asyncio
async def test_status_returns_loaded_false_when_model_missing() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"loaded": []})

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg())
    assert snap["loaded"] is False
    assert snap["reason"] == "model not in /v1/health.loaded[]"


@pytest.mark.asyncio
async def test_status_returns_loaded_false_when_no_model_assigned() -> None:
    def h(_: httpx.Request) -> httpx.Response:  # pragma: no cover — not hit
        raise AssertionError("client.health should never be called")

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg(model={"default": ""}))
    assert snap["loaded"] is False
    assert snap["reason"] == "no model assigned"


@pytest.mark.asyncio
async def test_status_never_raises_on_lemond_unavailable() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg())
    # Must not raise — dashboard hot path.
    assert snap["loaded"] is False
    assert snap["reason"] == "lemonade unavailable"
    assert "error" in snap


# ── health() (Provider ABC implementation) ────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_ok_true_on_2xx() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ready": True})

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.health(port=8081)
    assert result["ok"] is True
    assert result["health"] == {"ready": True}


@pytest.mark.asyncio
async def test_health_returns_ok_false_on_http_error() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "down"})

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.health(port=8081)
    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["error_type"] == LemonadeHTTPError.__name__


# ── ABC stubs (docker / systemd shape) ───────────────────────────────


def test_build_env_returns_informational_block() -> None:
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    env = provider.build_env(_slot_cfg(), {"path": "/var/lib/hal0/models/x.gguf"})
    assert env["HAL0_PROVIDER"] == "lemonade"
    assert env["HAL0_DEVICE"] == "gpu-rocm"
    assert env["HAL0_LEMONADE_LLAMACPP_BACKEND"] == "rocm"
    assert env["HAL0_MODEL_PATH"] == "/var/lib/hal0/models/x.gguf"


def test_image_ref_encodes_recipe_for_npu() -> None:
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    assert provider.image_ref(_slot_cfg(device="npu")) == "lemonade://recipe/flm"


def test_image_ref_encodes_llamacpp_backend_for_gpu() -> None:
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    assert provider.image_ref(_slot_cfg(device="gpu-vulkan")) == "lemonade://llamacpp/vulkan"


def test_container_spec_raises() -> None:
    """No per-slot container under Lemonade — caller fails loudly."""
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    with pytest.raises(NotImplementedError):
        provider.container_spec(_slot_cfg(), {})


def test_render_systemd_override_raises() -> None:
    """No per-slot systemd unit under Lemonade."""
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    with pytest.raises(NotImplementedError):
        provider.render_systemd_override(
            "primary",
            _slot_cfg(),
            {},
            env_file_path="/tmp/env",
        )


@pytest.mark.asyncio
async def test_infer_raises_not_implemented() -> None:
    provider = LemonadeProvider(client=_mock_client(lambda _: httpx.Response(200)))
    with pytest.raises(NotImplementedError):
        await provider.infer(port=8081, body={})


# ── provider registry integration ────────────────────────────────────


def test_get_provider_returns_lemonade_singleton() -> None:
    from hal0.providers import get_provider, lemonade_provider

    a = get_provider("lemonade")
    b = lemonade_provider()
    assert a is b
    assert isinstance(a, LemonadeProvider)


def test_legacy_providers_still_registered() -> None:
    """Anti-scope: PR-8 must NOT remove the v0.1.x provider singletons.
    PR-10 owns their retirement.
    """
    from hal0.providers import get_provider

    for name in ("llama-server", "flm", "moonshine", "kokoro", "comfyui"):
        provider = get_provider(name)
        assert provider is not None
        assert provider.__class__.__name__.lower().startswith(name.split("-")[0].lower())
