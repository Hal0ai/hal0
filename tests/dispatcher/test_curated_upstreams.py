"""Curated upstreams: the enabled list gates routing, ahead of every heuristic."""

from __future__ import annotations

import json

import pytest

from hal0.dispatcher.router import Dispatcher, NoRouteFound
from hal0.upstreams.catalog import ModelAmbiguous, ModelNotEnabled, UpstreamModel
from hal0.upstreams.registry import Upstream
from tests.dispatcher.test_router import (
    FakeModelRegistry,
    FakeUpstreamRegistry,
    make_remote,
    make_request,
    make_slot,
)


def make_curated(name: str = "mm", *rows: tuple[str, bool]) -> Upstream:
    rows = rows or (("MiniMax-M2.5", True),)
    return Upstream(
        name=name,
        kind="remote",
        url=f"http://{name}.invalid/v1",
        curated=True,
        models=tuple(UpstreamModel(id=mid, enabled=on) for mid, on in rows),
    )


def _cache_for(upstreams: list[Upstream], extra: dict[str, list[str]] | None = None):
    cache = {u.name: [m.id for m in u.models if m.enabled] for u in upstreams if u.curated}
    cache.update(extra or {})
    return cache


def _dispatcher(
    upstreams: list[Upstream],
    extra_cache: dict[str, list[str]] | None = None,
    routes: dict[str, str] | None = None,
    fetch=None,
) -> Dispatcher:
    async def online(_u: Upstream) -> bool:
        return True

    cache = _cache_for(upstreams, extra_cache)
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
async def test_canonical_id_routes_and_rewrites_body() -> None:
    d = _dispatcher([make_curated()])
    call = await d.dispatch(make_request(), body={"model": "mm/MiniMax-M2.5", "messages": []})
    assert call.upstream_name == "mm"
    assert json.loads(call.body)["model"] == "MiniMax-M2.5"
    assert call.requested_model == "mm/MiniMax-M2.5"
    assert call.resolved_model == "MiniMax-M2.5"
    assert call.resolution_path == "catalog:mm"
    assert call.target_url.endswith("/v1/chat/completions")


@pytest.mark.asyncio
async def test_bare_id_routes_when_unique() -> None:
    d = _dispatcher([make_curated()])
    call = await d.dispatch(make_request(), body={"model": "MiniMax-M2.5", "messages": []})
    assert call.upstream_name == "mm"
    assert call.resolution_path == "catalog:mm"
    assert json.loads(call.body)["model"] == "MiniMax-M2.5"


@pytest.mark.asyncio
async def test_ambiguous_bare_id_is_400() -> None:
    d = _dispatcher([make_curated("mm"), make_curated("mm2")])
    with pytest.raises(ModelAmbiguous) as err:
        await d.dispatch(make_request(), body={"model": "MiniMax-M2.5", "messages": []})
    assert err.value.status == 400
    assert err.value.code == "upstream.model_ambiguous"


@pytest.mark.asyncio
async def test_disabled_model_is_404_and_never_reaches_heuristics() -> None:
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    d = _dispatcher(
        [agent, make_curated("mm", ("MiniMax-M2.5", True), ("Off-Model", False))],
        extra_cache={"agent": ["agent-model"]},
    )
    with pytest.raises(ModelNotEnabled) as err:
        await d.dispatch(make_request(), body={"model": "mm/Off-Model", "messages": []})
    assert err.value.status == 404
    assert err.value.code == "upstream.model_not_enabled"
    with pytest.raises(ModelNotEnabled):
        await d.dispatch(make_request(), body={"model": "Off-Model", "messages": []})


@pytest.mark.asyncio
async def test_unlisted_model_on_known_provider_is_404() -> None:
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    d = _dispatcher([agent, make_curated()], extra_cache={"agent": ["agent-model"]})
    with pytest.raises(ModelNotEnabled):
        await d.dispatch(make_request(), body={"model": "mm/Never-Listed", "messages": []})


@pytest.mark.asyncio
async def test_local_registry_binding_wins_over_bare_upstream_id() -> None:
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    d = _dispatcher(
        [chat, make_curated()],
        extra_cache={"chat": ["MiniMax-M2.5"]},
        routes={"MiniMax-M2.5": "chat"},
    )
    call = await d.dispatch(make_request(), body={"model": "MiniMax-M2.5", "messages": []})
    assert call.upstream_name == "chat"
    assert call.resolution_path == "registry"


@pytest.mark.asyncio
async def test_local_slot_advertising_the_bare_id_wins_without_a_binding() -> None:
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    d = _dispatcher([chat, make_curated()], extra_cache={"chat": ["MiniMax-M2.5"]})
    call = await d.dispatch(make_request(), body={"model": "MiniMax-M2.5", "messages": []})
    assert call.upstream_name == "chat"
    assert call.resolution_path == "passthrough:chat"


@pytest.mark.asyncio
async def test_curated_upstream_is_never_cold_prefetched() -> None:
    fetched: list[str] = []

    async def fetch(u: Upstream) -> list[str]:
        fetched.append(u.name)
        return []

    d = _dispatcher([make_curated(), make_remote("plain", "http://plain.invalid/v1")], fetch=fetch)
    with pytest.raises(NoRouteFound):
        await d.dispatch(make_request(), body={"model": "unknown-model", "messages": []})
    assert fetched == ["plain"]


@pytest.mark.asyncio
async def test_stale_curated_cache_cannot_bypass_the_enabled_list() -> None:
    # Cache still holds an id the operator has since switched off.
    upstream = make_curated("mm", ("MiniMax-M2.5", True), ("Off-Model", False))
    d = _dispatcher([upstream], extra_cache={"mm": ["MiniMax-M2.5", "Off-Model"]})
    with pytest.raises(ModelNotEnabled):
        await d.dispatch(make_request(), body={"model": "Off-Model", "messages": []})


@pytest.mark.asyncio
async def test_uncurated_remote_still_passes_through_on_warm_cache() -> None:
    plain = make_remote("plain", "http://plain.invalid/v1")
    d = _dispatcher([make_curated(), plain], extra_cache={"plain": ["other-model"]})
    call = await d.dispatch(make_request(), body={"model": "other-model", "messages": []})
    assert call.upstream_name == "plain"
    assert call.resolution_path == "passthrough:plain"
