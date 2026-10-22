"""Pinned upstreams own their model ids and never fall through to other lanes."""

from __future__ import annotations

import json

import pytest

from hal0.dispatcher.router import Dispatcher, UpstreamUnavailable
from hal0.upstreams.registry import Upstream
from tests.dispatcher.test_router import (
    FakeModelRegistry,
    FakeUpstreamRegistry,
    make_remote,
    make_request,
    make_slot,
)


def make_pinned(name: str = "hermes.coder", owned: tuple[str, ...] = ("coder",)) -> Upstream:
    return Upstream(
        name=name,
        kind="remote",
        url="http://hermes.invalid/p/coder/v1",
        pinned=True,
        owned_models=owned,
    )


def _dispatcher(
    upstreams: list[Upstream],
    cache: dict[str, list[str]],
    fetch=None,
    routes: dict[str, str] | None = None,
) -> Dispatcher:
    async def online(_u: Upstream) -> bool:
        return True

    kwargs = {}
    if fetch is not None:
        kwargs["fetch_models"] = fetch
    return Dispatcher(
        upstream_registry=FakeUpstreamRegistry(upstreams),
        model_registry=FakeModelRegistry(routes=routes or {}),
        is_online=online,
        cached_models=lambda name: list(cache.get(name, [])),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_pinned_owned_model_routes_to_owner_when_cached() -> None:
    d = _dispatcher([make_pinned()], {"hermes.coder": ["coder"]})
    call = await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert call.resolution_path == "pinned:hermes.coder"
    assert call.upstream_name == "hermes.coder"
    assert call.target_url.endswith("/p/coder/v1/chat/completions")
    assert json.loads(call.body)["model"] == "coder"


@pytest.mark.asyncio
async def test_pinned_owned_model_cold_cache_fetches_then_routes() -> None:
    cache: dict[str, list[str]] = {}

    async def fetch(u: Upstream) -> list[str]:
        cache[u.name] = ["coder"]
        return ["coder"]

    d = _dispatcher([make_pinned()], cache, fetch=fetch)
    call = await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert call.resolution_path == "pinned:hermes.coder"


@pytest.mark.asyncio
async def test_pinned_owned_model_offline_raises_not_fallthrough() -> None:
    async def fetch(_u: Upstream) -> list[str]:
        raise ConnectionError("hermes down")

    d = _dispatcher([make_slot("agent"), make_pinned()], {}, fetch=fetch)
    with pytest.raises(UpstreamUnavailable) as exc:
        await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert exc.value.code == "dispatch.upstream_unavailable"
    assert exc.value.details == {"model": "coder", "upstream": "hermes.coder"}
    assert "offline" in str(exc.value)


@pytest.mark.asyncio
async def test_pinned_owned_id_beats_slot_name() -> None:
    slot = make_slot("coder", "http://127.0.0.1:8081/v1")
    d = _dispatcher(
        [slot, make_pinned()],
        {"hermes.coder": ["coder"], "coder": ["coder"]},
        routes={"coder": "coder"},
    )
    call = await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert call.resolution_path == "pinned:hermes.coder"


@pytest.mark.asyncio
async def test_unowned_model_unaffected_by_pinned_upstreams() -> None:
    chat = make_slot("chat")
    d = _dispatcher(
        [chat, make_pinned(), make_remote("other", "http://o.invalid/v1")],
        {"chat": ["qwen3-4b"], "hermes.coder": ["coder"]},
        routes={"qwen3-4b": "chat"},
    )
    call = await d.dispatch(make_request(), body={"model": "qwen3-4b", "messages": []})
    assert call.resolution_path == "registry"
    assert call.upstream_name == "chat"
