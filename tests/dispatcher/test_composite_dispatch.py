"""Regression tests for issue #422 — composite ``hal0`` upstream dispatch.

Two distinct bugs are locked down here:

  1. **Empty/ignored composite cache.** When the composite ``hal0`` upstream
     advertises a chat model, a chat request for that id must resolve via the
     registry/passthrough path — NOT fall through to the legacy heuristics
     (which raise :class:`NoRouteFound`, demoting the whole dispatcher layer to
     a raw Lemonade proxy and killing prompt-cache / instrumentation).

  2. **Readiness-gate masquerade.** ``hal0`` is a synthetic upstream with no
     backing slot. ``_slot_name_of`` used to fall back to ``upstream.name`` and
     hand ``forward()`` ``slot_name="hal0"``, so the swap-window gate called
     ``_current_state("hal0")`` → OFFLINE → a spurious 503. The composite must
     be exempt from the gate (and the SERVING wrap), and its forward must target
     the Lemonade gateway rather than hal0-api's own ``:8080`` (which would
     recurse forever).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from starlette.requests import Request

from hal0.dispatcher.router import (
    Dispatcher,
    UpstreamCall,
    _is_hal0_composite,
    _resolve_target_url,
    _slot_name_of,
)
from hal0.slots.state import SlotState
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── test doubles ──────────────────────────────────────────────────────────────


class _FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store: dict[str, Upstream] = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


class _FakeModelRegistry:
    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = routes or {}

    def route_for(self, model_id: str) -> str | None:
        return self._routes.get(model_id)


class _OfflineSlotManager:
    """SlotManager stand-in whose every slot reports OFFLINE.

    Records ``serving()`` entries so a test can prove the composite path
    never opens the SERVING context. ``_current_state`` returning OFFLINE
    would trip the readiness gate for any call carrying a non-empty
    ``slot_name`` — the composite must carry an empty one.
    """

    def __init__(self) -> None:
        self.serving_calls: list[str] = []

    def _current_state(self, _slot_name: str) -> SlotState:
        return SlotState.OFFLINE

    def serving(self, slot_name: str) -> Any:  # pragma: no cover - guard only
        self.serving_calls.append(slot_name)
        raise AssertionError(f"composite forward must not enter serving() (slot={slot_name!r})")


def _composite() -> Upstream:
    """The synthetic composite as ``_autoregister_slot_upstreams`` builds it."""
    return Upstream(
        name="hal0",
        kind="slot",
        url="http://127.0.0.1:8080/v1",
        slot_name=None,
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
    )


def _make_request(path: str = "/v1/chat/completions") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }
    return Request(scope)


# ── helper-level contracts ────────────────────────────────────────────────────


def test_is_hal0_composite_only_matches_the_synthetic_entry() -> None:
    assert _is_hal0_composite(_composite()) is True
    # A real per-slot upstream carries a slot_name.
    assert (
        _is_hal0_composite(
            Upstream(name="primary", kind="slot", url="http://x/v1", slot_name="primary")
        )
        is False
    )
    # A remote provider named "hal0" (operator override) is not the composite.
    assert _is_hal0_composite(Upstream(name="hal0", kind="remote", url="https://x/v1")) is False


def test_slot_name_of_exempts_composite_from_readiness_gate() -> None:
    """The composite yields an EMPTY slot_name so ``forward()`` skips the
    readiness gate + SERVING wrap (regression: it used to fall back to the
    name ``"hal0"`` and 503 against a non-existent slot)."""
    assert _slot_name_of(_composite()) == ""
    # Real slot still carries its name through.
    real = Upstream(name="primary", kind="slot", url="http://x/v1", slot_name="primary")
    assert _slot_name_of(real) == "primary"


def test_composite_forward_target_is_lemonade_gateway_not_self() -> None:
    """The composite must NOT forward to its own ``:8080`` URL (infinite
    recursion); it is redirected to the Lemonade gateway."""
    url = _resolve_target_url(_composite(), "/v1/chat/completions")
    assert url == "http://127.0.0.1:13305/v1/chat/completions"
    assert ":8080" not in url
    # Non-composite upstreams forward to their own url unchanged.
    real = Upstream(
        name="primary", kind="slot", url="http://127.0.0.1:8081/v1", slot_name="primary"
    )
    assert _resolve_target_url(real, "/v1/chat/completions") == (
        "http://127.0.0.1:8081/v1/chat/completions"
    )


def test_composite_forward_target_honours_env_override(monkeypatch: Any) -> None:
    monkeypatch.setenv("LEMONADE_BASE_URL", "http://10.0.0.5:9999")
    url = _resolve_target_url(_composite(), "/v1/chat/completions")
    assert url == "http://10.0.0.5:9999/v1/chat/completions"


# ── dispatch path ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_advertised_model_resolves_via_passthrough_not_legacy() -> None:
    """A chat request for a composite-advertised model resolves through the
    passthrough path (Step 2) — never the legacy fallthrough that demotes the
    dispatcher to a raw Lemonade proxy."""
    upstreams = _FakeUpstreamRegistry([_composite()])
    models = _FakeModelRegistry(routes={})
    cache = {"hal0": ["hermes-4-14b-q5km", "qwen3-coder-next-reap-40b-a3b-q4kxl"]}

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda name: cache.get(name, []),
    )

    call = await dispatcher.dispatch(
        _make_request(),
        body={"model": "hermes-4-14b-q5km", "messages": []},
    )

    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "hal0"
    assert call.resolution_path == "passthrough:hal0"
    # Readiness-gate exemption: empty slot_name.
    assert call.slot_name == ""
    # Forwarded to the Lemonade gateway, not back into hal0-api.
    assert call.target_url == "http://127.0.0.1:13305/v1/chat/completions"


@pytest.mark.asyncio
async def test_composite_call_skips_readiness_gate_on_forward() -> None:
    """End-to-end: a composite-resolved call forwards a 200 even though the
    SlotManager reports every slot OFFLINE — the gate must not fire, and the
    SERVING context must never open."""
    upstreams = _FakeUpstreamRegistry([_composite()])
    cache = {"hal0": ["hermes-4-14b-q5km"]}
    slot_mgr = _OfflineSlotManager()

    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"id": "chatcmpl-ok", "choices": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=_FakeModelRegistry(),
        cached_models=lambda name: cache.get(name, []),
        slot_manager=slot_mgr,  # type: ignore[arg-type]
        http_client=client,
    )
    try:
        call = await dispatcher.dispatch(
            _make_request(),
            body={"model": "hermes-4-14b-q5km", "messages": []},
        )
        resp = await dispatcher.forward(call)
        # No spurious 503 from the readiness gate.
        assert resp.status_code == 200
        # Forwarded to the gateway, not hal0-api itself.
        assert seen["url"] == "http://127.0.0.1:13305/v1/chat/completions"
        # SERVING context never opened (the stub raises if it is).
        assert slot_mgr.serving_calls == []
    finally:
        await dispatcher.aclose()
