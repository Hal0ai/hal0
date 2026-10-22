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


def make_pinned(
    name: str = "hermes.coder", owned: tuple[str, ...] = ("coder",), enabled: bool = True
) -> Upstream:
    return Upstream(
        name=name,
        kind="remote",
        url="http://hermes.invalid/p/coder/v1",
        pinned=True,
        owned_models=owned,
        enabled=enabled,
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


@pytest.mark.asyncio
async def test_pinned_owned_model_disabled_raises_not_fallthrough() -> None:
    pinned = make_pinned(enabled=False)
    d = _dispatcher([make_slot("agent"), pinned], {})
    with pytest.raises(UpstreamUnavailable) as exc:
        await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert exc.value.details == {"model": "coder", "upstream": "hermes.coder", "disabled": True}
    assert "disabled" in str(exc.value)


@pytest.mark.asyncio
async def test_pinned_owned_model_up_but_not_advertised() -> None:
    async def fetch(u: Upstream) -> list[str]:
        return ["other"]

    cache = {"hermes.coder": ["other"]}
    d = _dispatcher([make_slot("agent"), make_pinned()], cache, fetch=fetch)
    with pytest.raises(UpstreamUnavailable) as exc:
        await d.dispatch(make_request(), body={"model": "coder", "messages": []})
    assert "does not advertise" in str(exc.value)
    assert exc.value.details["advertised"] == ["other"]


@pytest.mark.asyncio
async def test_pinned_concurrent_cold_fetches_coalesce() -> None:
    import asyncio

    calls = 0
    cache: dict[str, list[str]] = {}

    async def fetch(u: Upstream) -> list[str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        cache[u.name] = ["coder"]
        return ["coder"]

    d = _dispatcher([make_pinned()], cache, fetch=fetch)
    results = await asyncio.gather(
        *[d.dispatch(make_request(), body={"model": "coder", "messages": []}) for _ in range(5)]
    )
    assert all(r.resolution_path == "pinned:hermes.coder" for r in results)
    assert calls == 1


@pytest.mark.asyncio
async def test_pinned_alias_in_cache_does_not_shadow_slot() -> None:
    """Ruling 9: a Hermes ``model_routes`` alias equal to a slot model never owns it."""
    chat = make_slot("chat")
    hermes = make_pinned("hermes", owned=("hermes-agent",))
    d = _dispatcher(
        [chat, hermes],
        {"chat": ["qwen3-4b"], "hermes": ["hermes-agent", "qwen3-4b"]},
        routes={"qwen3-4b": "chat"},
    )
    call = await d.dispatch(make_request(), body={"model": "qwen3-4b", "messages": []})
    assert call.resolution_path == "registry"
    assert call.upstream_name == "chat"


@pytest.mark.asyncio
async def test_cached_only_id_is_not_pinned() -> None:
    """An id only in a pinned upstream's cache resolves by the normal ladder."""
    hermes = make_pinned("hermes", owned=("hermes-agent",))
    d = _dispatcher([hermes], {"hermes": ["hermes-agent", "alias-x"]})
    call = await d.dispatch(make_request(), body={"model": "alias-x", "messages": []})
    assert not call.resolution_path.startswith("pinned:")
    assert call.upstream_name == "hermes"
