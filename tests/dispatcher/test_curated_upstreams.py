"""Curated upstreams: the enabled list gates routing.

An enabled row routes at the catalog step. A "not enabled" answer is a last
resort: the cache steps still run, and a bare id on a path-routed capability
(or a hal0 alias) still reaches the local slot, as it does without curation.
"""

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
    remote_lists: dict[str, list[str]] | None = None,
    fetched: list[str] | None = None,
) -> Dispatcher:
    """Dispatcher over ``upstreams``.

    ``remote_lists`` is what a cold prefetch of each upstream returns (it fills
    the cache); ``fetched`` collects the name of every upstream fetched.
    """

    async def online(_u: Upstream) -> bool:
        return True

    cache = _cache_for(upstreams, extra_cache)
    if fetch is None and (remote_lists is not None or fetched is not None):

        async def fetch(u: Upstream) -> list[str]:
            if fetched is not None:
                fetched.append(u.name)
            ids = list((remote_lists or {}).get(u.name, []))
            cache[u.name] = ids
            return ids

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
    with pytest.raises(ModelNotEnabled) as err:
        await d.dispatch(make_request(), body={"model": "Off-Model", "messages": []})
    assert err.value.details == {"provider": "mm", "model": "Off-Model"}


@pytest.mark.asyncio
async def test_unlisted_model_on_named_provider_is_404_not_the_agent_slot() -> None:
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    d = _dispatcher([agent, make_curated()], extra_cache={"agent": ["agent-model"]})
    with pytest.raises(ModelNotEnabled) as err:
        await d.dispatch(make_request(), body={"model": "mm/Never-Listed", "messages": []})
    assert err.value.details == {"provider": "mm", "model": "Never-Listed"}
    assert "not offered" in err.value.message


@pytest.mark.asyncio
async def test_disabled_canonical_ref_is_404() -> None:
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    d = _dispatcher(
        [agent, make_curated("mm", ("On", True), ("Off", False))],
        extra_cache={"agent": ["agent-model"]},
    )
    with pytest.raises(ModelNotEnabled) as err:
        await d.dispatch(make_request(), body={"model": "mm/Off", "messages": []})
    assert err.value.details == {"provider": "mm", "model": "Off"}


# ── "not enabled" is a last resort ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_disabled_bare_embedding_id_reaches_the_local_embed_slot() -> None:
    embed = make_slot("embed", "http://127.0.0.1:8090/v1")
    openai = make_curated("openai", ("gpt-5", True), ("text-embedding-3-small", False))
    d = _dispatcher([embed, openai], extra_cache={"embed": ["nomic-embed-text"]})
    call = await d.dispatch(
        make_request("/v1/embeddings"), body={"model": "text-embedding-3-small", "input": "x"}
    )
    assert call.upstream_name == "embed"
    assert call.resolution_path == "legacy_slot:embed"


@pytest.mark.asyncio
async def test_disabled_alias_on_speech_reaches_a_cold_local_tts_slot() -> None:
    tts = make_slot("tts", "http://127.0.0.1:8880/v1")
    openai = make_curated("openai", ("gpt-5", True), ("tts-1", False))
    d = _dispatcher([tts, openai])
    call = await d.dispatch(
        make_request("/v1/audio/speech"), body={"model": "tts-1", "input": "hi", "voice": "af"}
    )
    assert call.upstream_name == "tts"
    assert call.resolution_path == "legacy_slot:tts"


@pytest.mark.asyncio
async def test_disabled_alias_on_chat_reaches_the_local_heuristics() -> None:
    # "tts-1" is a hal0 alias name: it keeps reaching the local heuristics
    # even when a curated provider stores a disabled row with that id.
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    mm = make_curated("mm", ("MiniMax-M2.5", True), ("tts-1", False))
    d = _dispatcher([agent, mm])
    call = await d.dispatch(make_request(), body={"model": "tts-1", "messages": []})
    assert call.resolution_path == "legacy_slot:agent"


@pytest.mark.asyncio
async def test_canonical_disabled_embedding_ref_is_404_despite_local_embed_slot() -> None:
    embed = make_slot("embed", "http://127.0.0.1:8090/v1")
    openai = make_curated("openai", ("gpt-5", True), ("text-embedding-3-small", False))
    d = _dispatcher([embed, openai], extra_cache={"embed": ["nomic-embed-text"]})
    with pytest.raises(ModelNotEnabled) as err:
        await d.dispatch(
            make_request("/v1/embeddings"),
            body={"model": "openai/text-embedding-3-small", "input": "x"},
        )
    assert err.value.details == {"provider": "openai", "model": "text-embedding-3-small"}


@pytest.mark.asyncio
async def test_disabled_bare_id_is_found_by_cold_prefetch_of_an_unconverted_remote() -> None:
    plain = make_remote("plain", "http://plain.invalid/v1")
    mm = make_curated("mm", ("MiniMax-M2.5", True), ("shared-model", False))
    fetched: list[str] = []
    d = _dispatcher([mm, plain], remote_lists={"plain": ["shared-model"]}, fetched=fetched)
    call = await d.dispatch(make_request(), body={"model": "shared-model", "messages": []})
    assert call.resolution_path == "passthrough-prefetched:plain"
    assert fetched == ["plain"]


@pytest.mark.asyncio
async def test_unlisted_ref_is_found_by_cold_prefetch_of_an_unconverted_remote() -> None:
    orr = make_remote("openrouter", "http://openrouter.invalid/v1")
    openai = make_curated("openai", ("gpt-4o", True))
    fetched: list[str] = []
    d = _dispatcher([openai, orr], remote_lists={"openrouter": ["openai/gpt-5"]}, fetched=fetched)
    call = await d.dispatch(make_request(), body={"model": "openai/gpt-5", "messages": []})
    assert call.resolution_path == "passthrough-prefetched:openrouter"
    assert json.loads(call.body)["model"] == "openai/gpt-5"
    assert fetched == ["openrouter"]


@pytest.mark.asyncio
async def test_catalog_step_never_fetches() -> None:
    fetched: list[str] = []
    agent = make_slot("agent", "http://127.0.0.1:8081/v1")
    mm = make_curated("mm", ("MiniMax-M2.5", True), ("Off", False))
    d = _dispatcher([agent, mm], extra_cache={"agent": ["agent-model"]}, fetched=fetched)
    call = await d.dispatch(make_request(), body={"model": "MiniMax-M2.5", "messages": []})
    assert call.resolution_path == "catalog:mm"
    for model in ("Off", "mm/Off", "mm/Never-Listed"):
        with pytest.raises(ModelNotEnabled):
            await d.dispatch(make_request(), body={"model": model, "messages": []})
    assert fetched == []


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


# ── provider-name collisions with uncurated rows ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_warm_uncurated_row_keeps_its_exact_string(enabled: bool) -> None:
    orr = make_remote("openrouter", "http://openrouter.invalid/v1")
    openai = make_curated("openai", ("gpt-5", enabled))
    d = _dispatcher([orr, openai], extra_cache={"openrouter": ["openai/gpt-5"]})
    call = await d.dispatch(make_request(), body={"model": "openai/gpt-5", "messages": []})
    assert call.resolution_path == "passthrough:openrouter"
    assert json.loads(call.body)["model"] == "openai/gpt-5"


@pytest.mark.asyncio
async def test_agent_managed_upstream_row_keeps_its_exact_string() -> None:
    hermes = Upstream(
        name="hermes.x", kind="remote", url="http://h.invalid/v1", managed_by="agent:hermes"
    )
    openai = make_curated("openai", ("gpt-5", False))
    d = _dispatcher([hermes, openai], extra_cache={"hermes.x": ["openai/gpt-5"]})
    call = await d.dispatch(make_request(), body={"model": "openai/gpt-5", "messages": []})
    assert call.resolution_path == "passthrough:hermes.x"


@pytest.mark.asyncio
async def test_ref_enabled_only_on_another_curated_provider_routes_there() -> None:
    openai = make_curated("openai", ("gpt-4o", True))
    orr = make_curated("openrouter", ("openai/gpt-5", True))
    d = _dispatcher([openai, orr])
    call = await d.dispatch(make_request(), body={"model": "openai/gpt-5", "messages": []})
    assert call.resolution_path == "catalog:openrouter"
    assert json.loads(call.body)["model"] == "openai/gpt-5"


@pytest.mark.asyncio
async def test_listed_disabled_ref_with_no_other_holder_is_404() -> None:
    openai = make_curated("openai", ("gpt-5", False))
    d = _dispatcher([openai])
    with pytest.raises(ModelNotEnabled):
        await d.dispatch(make_request(), body={"model": "openai/gpt-5", "messages": []})
