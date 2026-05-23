"""SlotManager → LemonadeProvider bridge tests (PR-8 + PR-10).

Validates that the slot lifecycle methods on :class:`SlotManager`
route through :class:`LemonadeProvider`. PR-10 made this dispatch
unconditional; the ``HAL0_BACKEND`` env gate retired.

Caller-surface guarantee: the public method signatures on
SlotManager do NOT change — these tests exercise the same method
names + arg shapes as v0.1.x callers (api/routes, dispatcher,
orchestrator) use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotSpawnFailed, SlotState

# ── helpers ──────────────────────────────────────────────────────────


def _mock_provider(handler) -> LemonadeProvider:
    """Build a LemonadeProvider with an httpx-mocked client."""
    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    return LemonadeProvider(client=LemonadeClient(http_client=transport))


@pytest.fixture
def lemonade_env() -> None:
    """No-op fixture (PR-10 retired the HAL0_BACKEND gate).

    Kept as a parameter name so existing tests in this file don't need
    re-signing; Lemonade dispatch is unconditional now.
    """
    return None


@pytest.fixture
def stub_provider_in_registry(monkeypatch: pytest.MonkeyPatch):
    """Replace the singleton ``LemonadeProvider`` with a mock-backed one.

    Returns a factory: tests pass the handler they want to drive
    Lemonade's HTTP responses with. The factory installs the mock
    provider into ``hal0.providers._PROVIDERS`` so SlotManager's
    ``lemonade_provider()`` lookup finds it.
    """
    import hal0.providers as providers_mod

    original = providers_mod._PROVIDERS["lemonade"]

    def _install(handler) -> LemonadeProvider:
        provider = _mock_provider(handler)
        providers_mod._PROVIDERS["lemonade"] = provider
        return provider

    yield _install

    # Restore so subsequent tests get the real singleton back.
    providers_mod._PROVIDERS["lemonade"] = original


# ── spawn (load) bridge ──────────────────────────────────────────────


async def test_load_dispatches_via_lemonade_v1_load(
    slot_root: Path,
    lemonade_env: None,
    stub_provider_in_registry,
) -> None:
    """SlotManager.load should POST /v1/load with the slot's model + device."""
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            import json as _json

            captured["body"] = _json.loads(req.content.decode())
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(
                200,
                json={"loaded": [{"model_name": "qwen3-4b-q4_k_m"}]},
            )
        return httpx.Response(404)

    stub_provider_in_registry(h)

    mgr = SlotManager()
    snap = await mgr.load("primary")
    assert snap.state == SlotState.READY
    # The slot_root fixture writes ``backend = "vulkan"`` → device =
    # gpu-vulkan via the SlotConfig promotion.
    assert captured["body"]["model_name"] == "qwen3-4b-q4_k_m"
    assert captured["body"]["llamacpp_backend"] == "vulkan"


async def test_load_propagates_lemonade_load_error_as_slot_error(
    slot_root: Path,
    lemonade_env: None,
    stub_provider_in_registry,
) -> None:
    """A 5xx from /v1/load should land the slot in ERROR via SlotSpawnFailed."""

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            return httpx.Response(500, json={"detail": "evict-all triggered"})
        return httpx.Response(200, json={"loaded": []})

    stub_provider_in_registry(h)

    mgr = SlotManager()
    with pytest.raises(SlotSpawnFailed):
        await mgr.load("primary")
    snap = await mgr.status("primary")
    assert snap.state == SlotState.ERROR


async def test_unload_dispatches_via_lemonade_v1_unload(
    slot_root: Path,
    lemonade_env: None,
    stub_provider_in_registry,
) -> None:
    """SlotManager.unload should POST /v1/unload via the provider."""
    unload_calls: list[dict[str, Any]] = []
    # Mutable state so the same handler advertises "loaded" then "empty"
    # after the unload landing.
    loaded_state = {"models": [{"model_name": "qwen3-4b-q4_k_m"}]}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/load":
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/unload":
            import json as _json

            unload_calls.append(_json.loads(req.content.decode()))
            loaded_state["models"] = []
            return httpx.Response(200, json={"status": "unloaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": loaded_state["models"]})
        return httpx.Response(404)

    stub_provider_in_registry(h)

    mgr = SlotManager()
    await mgr.load("primary")
    snap = await mgr.unload("primary")
    assert snap.state == SlotState.OFFLINE
    assert unload_calls == [{"model_name": "qwen3-4b-q4_k_m"}]


async def test_swap_under_lemonade_loads_new_model_name(
    slot_root: Path,
    lemonade_env: None,
    stub_provider_in_registry,
) -> None:
    """SlotManager.swap should unload then load with the override model."""
    captured_loads: list[dict[str, Any]] = []
    loaded_state = {"models": [{"model_name": "qwen3-4b-q4_k_m"}]}

    def h(req: httpx.Request) -> httpx.Response:
        import json as _json

        if req.url.path == "/v1/load":
            body = _json.loads(req.content.decode())
            captured_loads.append(body)
            loaded_state["models"] = [{"model_name": body["model_name"]}]
            return httpx.Response(200, json={"status": "loaded"})
        if req.url.path == "/v1/unload":
            loaded_state["models"] = []
            return httpx.Response(200, json={"status": "unloaded"})
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": loaded_state["models"]})
        return httpx.Response(404)

    stub_provider_in_registry(h)

    mgr = SlotManager()
    await mgr.load("primary")
    await mgr.swap("primary", "hermes-2-pro-llama-3-8b")
    # Two /v1/load calls: initial + swap override.
    assert len(captured_loads) == 2
    assert captured_loads[-1]["model_name"] == "hermes-2-pro-llama-3-8b"


# ── status() reconcile under Lemonade ────────────────────────────────


async def test_is_active_resolves_via_health_loaded_list(
    slot_root: Path,
    lemonade_env: None,
    stub_provider_in_registry,
) -> None:
    """_is_active reads /v1/health.loaded[] under Lemonade.

    Drives the status() reconciler: a slot whose model appears in
    /v1/health is treated as active, regardless of systemctl.
    """
    loaded_state = {"models": [{"model_name": "qwen3-4b-q4_k_m"}]}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": loaded_state["models"]})
        if req.url.path == "/v1/load":
            return httpx.Response(200, json={"status": "loaded"})
        return httpx.Response(404)

    stub_provider_in_registry(h)
    mgr = SlotManager()

    # Active branch
    assert await mgr._is_active("primary") is True
    # Inactive branch
    loaded_state["models"] = []
    assert await mgr._is_active("primary") is False
