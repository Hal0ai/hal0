"""Unit tests for ``hal0.providers.lemonade.LemonadeProvider``.

PR-8 + PR-10 capability dispatch wiring. Covers:

  * ``device_to_backend`` mapping (plan §4.1 + ADR-0008 §6)
  * ``LemonadeProvider.load`` body construction → ``LemonadeClient.load``
  * ``LemonadeProvider.unload`` idempotence + noop on modelless slot
  * ``LemonadeProvider.status`` derivation from ``/v1/health.loaded[]``
  * ``LemonadeProvider.health`` envelope shape (ok=True/False)
  * ABC stub behaviour (``container_spec`` /
    ``render_systemd_override`` raise; ``build_env`` /
    ``image_ref`` / ``start_cmd`` return informational data)

PR-10 deleted the ``lemonade_active`` env gate (ADR-0008 §1: Lemonade
is the sole backend; no toggle).

Mocks ``LemonadeClient`` via ``httpx.MockTransport`` — same pattern as
``tests/lemonade/test_client.py``. We exercise the full request path
(serialisation + parsing) so a bug in either layer surfaces here too.
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx
import pytest

import hal0.providers.lemonade as lemonade_mod
from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import LemonadeHTTPError, LemonadeLoadError
from hal0.providers.lemonade import (
    LemonadeProvider,
    device_to_backend,
    resolve_actual_backend,
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
        if req.url.path == "/v1/health":
            # Model IS loaded — unload should proceed to /v1/unload.
            return httpx.Response(
                200,
                json={
                    "loaded": [
                        {
                            "model_name": "hermes-4-14b",
                            "backend_url": "http://127.0.0.1:14000",
                            "last_use": 1715000000.0,
                        }
                    ]
                },
            )
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


@pytest.mark.asyncio
async def test_unload_is_noop_when_model_not_in_loaded() -> None:
    """Seed-but-never-loaded slots: avoid the Lemonade 404 by probing
    /v1/health first. Regression for PR #270 follow-up."""
    saw_unload = False

    def h(req: httpx.Request) -> httpx.Response:
        nonlocal saw_unload
        if req.url.path == "/v1/health":
            # Empty loaded[] — the slot's model_default has never been loaded.
            return httpx.Response(200, json={"loaded": []})
        if req.url.path == "/v1/unload":
            saw_unload = True
        raise AssertionError(f"unexpected path: {req.url.path}")

    provider = LemonadeProvider(client=_mock_client(h))
    result = await provider.unload(_slot_cfg())
    assert result["ok"] is True
    assert result["noop"] == "model not currently loaded"
    assert result["model_name"] == "hermes-4-14b"
    assert saw_unload is False


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


# ── resolve_actual_backend() (B2 — ADR-0022) ─────────────────────────


def _patch_actual_backend_chain(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pid: int | None,
    exe: str | None,
) -> None:
    """Stub the port→PID→exe chain so resolve_actual_backend is deterministic.

    The backend_url's port is parsed by the real ``_port_from_backend_url``;
    we only stub the listener lookup + exe resolution so the test doesn't
    depend on a live llama-server.
    """
    monkeypatch.setattr(lemonade_mod, "_pid_listening_on_port", lambda _port: pid)
    monkeypatch.setattr(lemonade_mod, "_exe_path_for_pid", lambda _pid: exe)


def test_resolve_actual_backend_vulkan(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_actual_backend_chain(
        monkeypatch,
        pid=4242,
        exe="/var/lib/hal0/lemonade/bin/llamacpp/vulkan/llama-server",
    )
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14002/v1"}
    assert resolve_actual_backend(entry) == "vulkan"


def test_resolve_actual_backend_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_actual_backend_chain(
        monkeypatch,
        pid=4243,
        exe="/var/lib/hal0/lemonade/bin/llamacpp/rocm-stable/llama-server",
    )
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14003/v1"}
    assert resolve_actual_backend(entry) == "rocm"


def test_resolve_actual_backend_rocmfp4_fork(monkeypatch: pytest.MonkeyPatch) -> None:
    # The custom ROCmFP4 fork binary has no /rocm-stable/ marker; it must
    # still classify as rocm rather than fall through to the cpu fallback.
    _patch_actual_backend_chain(
        monkeypatch,
        pid=4245,
        exe="/opt/rocmfp4-llama/bin/llama-server",
    )
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:8001/v1"}
    assert resolve_actual_backend(entry) == "rocm"


def test_resolve_actual_backend_cpu_when_no_gpu_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_actual_backend_chain(
        monkeypatch,
        pid=4244,
        exe="/usr/local/bin/llama-server",
    )
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14004"}
    assert resolve_actual_backend(entry) == "cpu"


def test_resolve_actual_backend_none_when_no_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_actual_backend_chain(monkeypatch, pid=None, exe=None)
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14005/v1"}
    assert resolve_actual_backend(entry) is None


def test_resolve_actual_backend_none_when_exe_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_actual_backend_chain(monkeypatch, pid=4246, exe=None)
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14006/v1"}
    assert resolve_actual_backend(entry) is None


def test_resolve_actual_backend_none_when_no_backend_url() -> None:
    assert resolve_actual_backend({"model_name": "m"}) is None
    assert resolve_actual_backend({"model_name": "m", "backend_url": ""}) is None


def test_resolve_actual_backend_none_on_non_dict() -> None:
    assert resolve_actual_backend(None) is None
    assert resolve_actual_backend("not-a-dict") is None  # type: ignore[arg-type]


def test_resolve_actual_backend_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any exception in the introspection chain returns None, not a raise."""

    def _boom(_port: int):
        raise RuntimeError("listener lookup exploded")

    monkeypatch.setattr(lemonade_mod, "_pid_listening_on_port", _boom)
    entry = {"model_name": "m", "backend_url": "http://127.0.0.1:14007/v1"}
    assert resolve_actual_backend(entry) is None


# ── status() backend fields (B2 — ADR-0022) ───────────────────────────


@pytest.mark.asyncio
async def test_status_adds_declared_backend_always_when_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "loaded": [
                    {"model_name": "hermes-4-14b", "backend_url": "http://127.0.0.1:14000/v1"}
                ]
            },
        )

    # Undeterminable actual backend → declared present, actual/mismatch absent.
    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: None)
    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg(device="gpu-vulkan"))
    assert snap["loaded"] is True
    assert snap["declared_backend"] == "vulkan"
    assert "actual_backend" not in snap
    assert "backend_mismatch" not in snap


@pytest.mark.asyncio
async def test_status_reports_mismatch_when_declared_vulkan_actual_rocm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "loaded": [
                    {"model_name": "hermes-4-14b", "backend_url": "http://127.0.0.1:14000/v1"}
                ]
            },
        )

    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "rocm")
    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg(device="gpu-vulkan"))
    assert snap["declared_backend"] == "vulkan"
    assert snap["actual_backend"] == "rocm"
    assert snap["backend_mismatch"] is True


@pytest.mark.asyncio
async def test_status_no_mismatch_when_declared_equals_actual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "loaded": [
                    {"model_name": "hermes-4-14b", "backend_url": "http://127.0.0.1:14000/v1"}
                ]
            },
        )

    monkeypatch.setattr(lemonade_mod, "resolve_actual_backend", lambda _e: "vulkan")
    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg(device="gpu-vulkan"))
    assert snap["actual_backend"] == "vulkan"
    assert snap["backend_mismatch"] is False


@pytest.mark.asyncio
async def test_status_omits_backend_fields_when_not_loaded() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"loaded": []})

    provider = LemonadeProvider(client=_mock_client(h))
    snap = await provider.status(_slot_cfg(device="gpu-vulkan"))
    assert snap["loaded"] is False
    assert "declared_backend" not in snap
    assert "actual_backend" not in snap
    assert "backend_mismatch" not in snap


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
