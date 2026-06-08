"""Unit tests for ``hal0.dispatcher.router.Dispatcher``.

Covers all four resolution paths from PLAN.md §3:

    1. registry            — exact ModelRegistry binding
    2. passthrough         — upstream's cached /v1/models already has the id
    3. cold-cache prefetch — fanout + recheck (Tier 2 timeout + Tier 3 single-flight)
    4. legacy fallback     — path/name heuristics in proxy.py

Plus the structured-envelope assertions for every ``dispatch.*`` error code
(PLAN.md §5 Tier 1 — no silent swallowing).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from hal0.api.middleware import error_codes
from hal0.dispatcher.proxy import LegacyResolutionFailed
from hal0.dispatcher.router import (
    Dispatcher,
    NoRouteFound,
    RegistryLoadFailed,
    UnknownUpstream,
    UpstreamCall,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── test doubles ──────────────────────────────────────────────────────────────


class FakeUpstreamRegistry(UpstreamRegistry):
    """In-memory UpstreamRegistry usable as a drop-in for unit tests."""

    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store: dict[str, Upstream] = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


class FakeModelRegistry:
    """Minimal ModelRegistry surface — only what Dispatcher uses."""

    def __init__(
        self,
        routes: dict[str, str] | None = None,
        raise_on: Exception | None = None,
    ) -> None:
        self._routes = routes or {}
        self._raise = raise_on

    def route_for(self, model_id: str) -> str | None:
        if self._raise is not None:
            raise self._raise
        return self._routes.get(model_id)


def make_request(path: str = "/v1/chat/completions", method: str = "POST") -> Request:
    """Build a minimal Starlette Request without going through a real client.

    Body parsing is short-circuited by passing ``body`` directly to
    ``Dispatcher.dispatch()``.
    """
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer client-secret"),
        ],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }
    return Request(scope)


def make_slot(name: str = "chat", url: str = "http://127.0.0.1:8081/v1") -> Upstream:
    return Upstream(name=name, kind="slot", url=url, slot_name=name)


def make_remote(name: str, url: str) -> Upstream:
    return Upstream(name=name, kind="remote", url=url)


# ── 1. registry-exact path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registry_exact_routes_to_bound_upstream_when_online() -> None:
    primary = make_slot("chat", "http://127.0.0.1:8081/v1")
    upstreams = FakeUpstreamRegistry([primary])
    models = FakeModelRegistry(routes={"qwen3-4b": "chat"})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        cached_models=lambda name: ["qwen3-4b"] if name == "chat" else [],
    )

    call = await dispatcher.dispatch(
        make_request(),
        body={"model": "qwen3-4b", "messages": []},
    )

    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "chat"
    assert call.resolution_path == "registry"
    assert call.target_url == "http://127.0.0.1:8081/v1/chat/completions"
    # Body model is preserved when it matches what the slot serves.
    assert json.loads(call.body)["model"] == "qwen3-4b"


@pytest.mark.asyncio
async def test_registry_remaps_body_when_requested_model_not_in_slot() -> None:
    """Slot-as-truth: rewrite body.model to what the slot actually has loaded."""
    primary = make_slot("chat")
    upstreams = FakeUpstreamRegistry([primary])
    models = FakeModelRegistry(routes={"qwen3-4b": "chat"})

    async def online(_u: Upstream) -> bool:
        return True

    # Slot advertises a DIFFERENT model from the registry binding.
    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        cached_models=lambda name: ["different-model"] if name == "chat" else [],
    )

    call = await dispatcher.dispatch(
        make_request(),
        body={"model": "qwen3-4b", "messages": []},
    )
    assert call.resolution_path == "registry"
    assert json.loads(call.body)["model"] == "different-model"


@pytest.mark.asyncio
async def test_registry_binding_to_unknown_upstream_raises_typed_error() -> None:
    upstreams = FakeUpstreamRegistry([])  # nothing registered
    models = FakeModelRegistry(routes={"qwen3-4b": "ghost-slot"})
    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)

    with pytest.raises(UnknownUpstream) as exc:
        await dispatcher.dispatch(make_request(), body={"model": "qwen3-4b"})
    assert exc.value.code == "dispatch.unknown_upstream"
    assert exc.value.details["upstream"] == "ghost-slot"


@pytest.mark.asyncio
async def test_registry_load_failure_raises_typed_error() -> None:
    """Tier 1: registry read errors must NOT be silently swallowed."""
    upstreams = FakeUpstreamRegistry([make_slot("chat")])
    models = FakeModelRegistry(raise_on=RuntimeError("disk on fire"))
    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)

    with pytest.raises(RegistryLoadFailed) as exc:
        await dispatcher.dispatch(make_request(), body={"model": "qwen3-4b"})
    assert exc.value.code == "dispatch.registry_unavailable"
    assert exc.value.status == 503


# ── 2. passthrough path ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_passthrough_when_upstream_cache_has_model() -> None:
    remote = make_remote("openrouter", "https://openrouter.ai/api/v1")
    upstreams = FakeUpstreamRegistry([make_slot("chat"), remote])
    # No registry binding for this id.
    models = FakeModelRegistry(routes={})

    cache = {"openrouter": ["meta/llama-3.1-405b"]}
    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda name: cache.get(name, []),
    )

    call = await dispatcher.dispatch(
        make_request(),
        body={"model": "meta/llama-3.1-405b"},
    )
    assert call.upstream_name == "openrouter"
    assert call.resolution_path == "passthrough:openrouter"


# ── 3. cold-cache prefetch path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cold_cache_prefetch_populates_then_routes() -> None:
    """Tier 3: the resolution path explicitly tags ``passthrough-prefetched``."""
    remote = make_remote("anthropic", "https://api.anthropic.com/v1")
    upstreams = FakeUpstreamRegistry([remote])
    models = FakeModelRegistry(routes={})

    # cache starts empty, fetch_models populates it.
    cache: dict[str, list[str]] = {"anthropic": []}

    async def fetch(u: Upstream) -> list[str]:
        cache[u.name] = ["claude-opus-4-7"]
        return cache[u.name]

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda name: cache.get(name, []),
        fetch_models=fetch,
    )

    call = await dispatcher.dispatch(
        make_request(),
        body={"model": "claude-opus-4-7"},
    )
    assert call.resolution_path == "passthrough-prefetched:anthropic"
    assert call.upstream_name == "anthropic"


@pytest.mark.asyncio
async def test_prefetch_respects_configurable_timeout() -> None:
    """Tier 2: prefetch_timeout_s is configurable; we set a tight one."""
    remote = make_remote("slow", "https://slow.example.com/v1")
    upstreams = FakeUpstreamRegistry([remote])
    models = FakeModelRegistry(routes={})

    async def slow_fetch(_u: Upstream) -> list[str]:
        await asyncio.sleep(5.0)  # would hang past the timeout
        return ["never-arrives"]

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda _name: [],
        fetch_models=slow_fetch,
        prefetch_timeout_s=0.05,
    )

    # No legacy slot upstream exists either, so we expect NoRouteFound after timeout.
    with pytest.raises(NoRouteFound) as exc:
        await dispatcher.dispatch(make_request(), body={"model": "never-arrives"})
    assert exc.value.code == "dispatch.no_route"


# ── 4. legacy fallback path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_fallback_routes_to_primary_when_nothing_else_matches() -> None:
    primary = make_slot("chat")
    upstreams = FakeUpstreamRegistry([primary])
    models = FakeModelRegistry(routes={})  # no binding

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)
    call = await dispatcher.dispatch(
        make_request(),
        body={"model": "some-unknown-model"},
    )
    assert call.resolution_path == "legacy_slot:chat"
    assert call.upstream_name == "chat"


@pytest.mark.asyncio
async def test_legacy_fallback_routes_embeddings_to_embed_slot() -> None:
    embed = make_slot("embed", "http://127.0.0.1:8082/v1")
    upstreams = FakeUpstreamRegistry([embed])
    models = FakeModelRegistry(routes={})

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)
    call = await dispatcher.dispatch(
        make_request(path="/v1/embeddings"),
        body={"input": "hello"},
    )
    assert call.resolution_path == "legacy_slot:embed"
    assert call.upstream_name == "embed"


@pytest.mark.asyncio
async def test_legacy_fallback_with_no_primary_raises_typed_no_route() -> None:
    """When even legacy resolution can't find a slot, raise typed NoRouteFound."""
    upstreams = FakeUpstreamRegistry([])  # nothing registered
    models = FakeModelRegistry(routes={})

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)
    with pytest.raises(NoRouteFound) as exc:
        await dispatcher.dispatch(make_request(), body={"model": "anything"})
    assert exc.value.code == "dispatch.no_route"
    assert isinstance(exc.value.__cause__, LegacyResolutionFailed)


# ── path defaults ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_model_id_uses_path_default_for_embeddings() -> None:
    embed = make_slot("embed")
    upstreams = FakeUpstreamRegistry([embed])
    models = FakeModelRegistry(routes={"embed": "embed"})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        cached_models=lambda name: ["embed"] if name == "embed" else [],
    )
    call = await dispatcher.dispatch(
        make_request(path="/v1/embeddings"),
        body={"input": "x"},
    )
    assert call.upstream_name == "embed"
    assert call.resolution_path == "registry"


# ── structured error envelope ────────────────────────────────────────────────


def test_dispatch_errors_render_structured_envelope() -> None:
    """Assert every dispatch.* error round-trips through the error middleware
    into the documented ``{"error": {"code": "dispatch.*", ...}}`` envelope.
    """
    app = FastAPI()
    error_codes.install(app)

    from hal0.dispatcher.router import (
        DispatchError,
        NoRouteFound,
        RegistryLoadFailed,
        UnknownUpstream,
    )

    @app.get("/raises/no-route")
    async def _no_route() -> None:
        raise NoRouteFound("nothing found", details={"model": "x"})

    @app.get("/raises/unknown")
    async def _unknown() -> None:
        raise UnknownUpstream("bad bind", details={"upstream": "ghost"})

    @app.get("/raises/regfail")
    async def _regfail() -> None:
        raise RegistryLoadFailed("registry down")

    @app.get("/raises/base")
    async def _base() -> None:
        raise DispatchError("generic dispatch error")

    client = TestClient(app)

    cases: list[tuple[str, int, str]] = [
        ("/raises/no-route", 404, "dispatch.no_route"),
        ("/raises/unknown", 400, "dispatch.unknown_upstream"),
        ("/raises/regfail", 503, "dispatch.registry_unavailable"),
        ("/raises/base", 500, "dispatch.error"),
    ]
    for path, status, code in cases:
        resp = client.get(path)
        assert resp.status_code == status, (path, resp.json())
        payload: dict[str, Any] = resp.json()
        assert "error" in payload, payload
        assert payload["error"]["code"] == code
        assert "message" in payload["error"]
        assert "details" in payload["error"]
        assert payload["error"]["code"].startswith("dispatch.")


# ── decision logging ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_decision_logging_runs_on_every_resolution() -> None:
    """Tier 2: every dispatch decision emits one structured log line.

    We don't pin the exact log content — just verify the logger fires.
    """
    import structlog

    from hal0.dispatcher import router as router_module

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append((event_dict.get("event", ""), dict(event_dict)))
        # Raise DropEvent so structlog short-circuits before hitting the default
        # PrintLogger (which can't handle structured kwargs).
        raise structlog.DropEvent

    old = structlog.get_config()
    structlog.configure(processors=[_capture], cache_logger_on_first_use=False)
    # If a previously-loaded module (Cognee in tests/memory) ran
    # ``structlog.configure(cache_logger_on_first_use=True)``, the
    # dispatcher's module-level BoundLoggerLazyProxy has cached its
    # ``.bind`` to the *old* processor list — our re-configure above
    # does NOT retroactively rebind cached proxies. Drop the cached
    # bind so the next ``log.info`` call materializes a fresh
    # BoundLogger that picks up our ``_capture`` processor.
    cached_bind = router_module.log.__dict__.pop("bind", None)
    try:
        primary = make_slot("chat")
        upstreams = FakeUpstreamRegistry([primary])
        models = FakeModelRegistry(routes={})
        dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)
        await dispatcher.dispatch(make_request(), body={"model": "anything"})
    finally:
        # Restore the dispatcher's prior cached bind (if any) before
        # restoring the global config, so subsequent tests see the
        # same proxy state they would have seen without this test.
        if cached_bind is not None:
            router_module.log.bind = cached_bind  # type: ignore[method-assign]
        structlog.configure(**old)

    events = [e for e, _ in captured]
    assert "dispatch.decision" in events


# ── B1: backend-aware lazy-load on forward() (ADR-0022) ────────────────────────


class _FakeSlotManager:
    """Minimal SlotManager surface for the forward() lazy-load gate.

    Tracks whether ``load`` was called and reports a fixed slot state via
    ``_current_state`` so we can drive both the cold-miss and already-loaded
    branches of ``_ensure_slot_loaded_backend_aware``.
    """

    def __init__(self, state: Any) -> None:
        self._state = state
        self.load_calls: list[str] = []

    def _current_state(self, name: str) -> Any:
        return self._state

    async def load(self, slot_name: str, model_id: str | None = None) -> None:
        self.load_calls.append(slot_name)
        # Stays in the same (non-ready) state — emulates a load still in
        # flight so the subsequent ready-check raises SlotLoading.

    def serving(self, slot_name: str):
        manager = self

        class _Ctx:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *a: Any) -> None:
                return None

        _ = manager
        return _Ctx()


@pytest.mark.asyncio
async def test_forward_cold_miss_kicks_backend_aware_load_then_raises_loading() -> None:
    """Slot resolved + model NOT loaded → SlotManager.load called, SlotLoading raised."""
    from hal0.dispatcher.router import SlotLoading
    from hal0.slots.state import SlotState

    sm = _FakeSlotManager(state=SlotState.OFFLINE)
    dispatcher = Dispatcher(slot_manager=sm)  # type: ignore[arg-type]
    call = UpstreamCall(
        upstream_name="chat",
        target_url="http://127.0.0.1:8081/v1/chat/completions",
        body=json.dumps({"model": "chat"}).encode(),
        slot_name="chat",
    )
    with pytest.raises(SlotLoading):
        await dispatcher.forward(call)
    # The backend-aware load was kicked on the cold miss.
    assert sm.load_calls == ["chat"]
    # And the body was NOT mutated (no llamacpp_backend injection).
    assert json.loads(call.body) == {"model": "chat"}


@pytest.mark.asyncio
async def test_forward_already_loaded_skips_load_and_forwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model already loaded (slot READY) → no load call, normal forward."""
    from hal0.dispatcher import router as router_mod
    from hal0.slots.state import SlotState

    sm = _FakeSlotManager(state=SlotState.READY)
    dispatcher = Dispatcher(slot_manager=sm)  # type: ignore[arg-type]
    call = UpstreamCall(
        upstream_name="chat",
        target_url="http://127.0.0.1:8081/v1/chat/completions",
        body=json.dumps({"model": "chat"}).encode(),
        slot_name="chat",
    )

    forwarded: dict[str, Any] = {}

    async def _fake_forward_with_serving(self: Any, c: UpstreamCall):
        forwarded["called"] = True
        forwarded["body"] = c.body
        from fastapi.responses import Response

        return Response(content=b"ok", status_code=200)

    monkeypatch.setattr(router_mod.Dispatcher, "_forward_with_serving", _fake_forward_with_serving)
    resp = await dispatcher.forward(call)
    assert resp.status_code == 200
    # No load on the warm path.
    assert sm.load_calls == []
    assert forwarded.get("called") is True
    # Body never carries an injected llamacpp_backend.
    assert json.loads(forwarded["body"]) == {"model": "chat"}
