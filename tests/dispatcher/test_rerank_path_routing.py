"""Tests for rerank path-based routing to the ``rerank`` slot.

Phase C task C4 — ``/v1/rerankings`` (and ``/v1/rerank``) path-routes to the
dedicated ``rerank`` slot (vulkan llama-server, --reranking, port 8083) at
both the router (_default_for_path / _RERANK_DEFAULT) and resolve_by_capability
(Step 4) layers.

Key seam: llama-server serves ``POST /rerank`` and ``POST /v1/rerank``
natively — NOT ``/v1/rerankings``.  The dispatcher rewrites the outgoing
path via ``_UPSTREAM_PATH_REWRITES = {"/v1/rerankings": "/v1/rerank"}``,
applied in ``_join_url`` before building the forward URL.

Mirrors test_tts_path_routing.py in structure.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from hal0.dispatcher.router import (
    Dispatcher,
    LegacyResolutionFailed,
    NoRouteFound,
    UpstreamCall,
    resolve_by_capability,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── test doubles (same pattern as test_tts_path_routing.py) ──────────────────


class FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store: dict[str, Upstream] = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


class FakeModelRegistry:
    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = routes or {}

    def route_for(self, model_id: str) -> str | None:
        return self._routes.get(model_id)


def make_request(path: str = "/v1/rerankings", method: str = "POST") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer test-token"),
        ],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }
    return Request(scope)


def make_slot(name: str, url: str = "http://127.0.0.1:8082/v1") -> Upstream:
    return Upstream(name=name, kind="slot", url=url, slot_name=name)


def make_remote_rerank(port: int = 8083) -> Upstream:
    """A kind='remote' container upstream registered as 'rerank'."""
    return Upstream(
        name="rerank",
        kind="remote",
        url=f"http://127.0.0.1:{port}/v1",
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
        slot_name="rerank",  # container-backed remote
    )


# ── resolve_by_capability — capability/path routing (Rule 2) ──────────────────


def test_rerankings_path_pins_to_rerank_slot() -> None:
    """Legacy fallback Rule 2: /v1/rerankings in path → rerank slot.

    Phase C: /v1/rerankings is hal0's public OpenAI-compat reranking route;
    the dedicated rerank slot (not embed) absorbs it.
    """
    rerank = make_slot("rerank", "http://127.0.0.1:8083/v1")
    upstreams = FakeUpstreamRegistry([rerank])

    upstream = resolve_by_capability(
        path="/v1/rerankings",
        body={"model": "bge-reranker-v2-m3", "query": "foo", "documents": ["bar"]},
        upstreams=upstreams,
    )
    assert upstream.name == "rerank"


def test_rerank_fragment_no_longer_pins_embed() -> None:
    """/v1/rerankings must NOT pin to embed — only to the rerank slot.

    Before Phase C, /rerank was in _EMBED_PATHS so it fell through to embed.
    Now _RERANK_PATHS contains both /rerankings and /rerank and the candidate
    is 'rerank', not 'embed'.
    """
    rerank = make_slot("rerank", "http://127.0.0.1:8083/v1")
    embed = make_slot("embed", "http://127.0.0.1:8086/v1")
    upstreams = FakeUpstreamRegistry([rerank, embed])

    upstream = resolve_by_capability(
        path="/v1/rerankings",
        body={"model": "bge-reranker-v2-m3", "query": "foo", "documents": ["bar"]},
        upstreams=upstreams,
    )
    assert upstream.name == "rerank"
    assert upstream.name != "embed"


def test_embeddings_still_pin_to_embed() -> None:
    """/v1/embeddings still routes to embed — Phase C does NOT change embed routing."""
    embed = make_slot("embed", "http://127.0.0.1:8086/v1")
    upstreams = FakeUpstreamRegistry([embed])

    upstream = resolve_by_capability(
        path="/v1/embeddings",
        body={"input": "hello", "model": "bge-large-en"},
        upstreams=upstreams,
    )
    assert upstream.name == "embed"


# ── router._default_for_path / _RERANK_DEFAULT ───────────────────────────────


def test_router_default_for_rerank_path_is_rerank() -> None:
    """_default_for_path('/v1/rerankings') returns 'rerank', not 'embed'."""
    rerank = make_slot("rerank", "http://127.0.0.1:8083/v1")
    upstreams = FakeUpstreamRegistry([rerank])
    models = FakeModelRegistry(routes={})

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)
    result = dispatcher._default_for_path("/v1/rerankings")
    assert result == "rerank"
    assert result != "embed"


# ── outgoing path rewrite (/v1/rerankings → /v1/rerank) ──────────────────────


@pytest.mark.asyncio
async def test_forward_path_rewritten_to_v1_rerank() -> None:
    """Full dispatch(): outgoing upstream URL uses /v1/rerank, not /v1/rerankings.

    llama-server serves POST /rerank natively (not /rerankings).  The dispatcher
    must rewrite the path before building target_url.  This mirrors how
    test_dispatch_kokoro_v1_resolves_container_remote asserts target_url.
    """
    container_rerank = make_remote_rerank(port=8083)
    upstreams = FakeUpstreamRegistry([container_rerank])
    models = FakeModelRegistry(routes={})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        # Container advertises its model id so Step 0 preemption fires.
        cached_models=lambda name: ["bge-reranker-v2-m3"] if name == "rerank" else [],
    )

    call = await dispatcher.dispatch(
        make_request(path="/v1/rerankings"),
        body={"model": "bge-reranker-v2-m3", "query": "foo", "documents": ["bar"]},
    )
    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "rerank"
    # THE critical assertion: outgoing path must be /v1/rerank, not /v1/rerankings
    assert call.target_url == "http://127.0.0.1:8083/v1/rerank"
    assert "/rerankings" not in call.target_url
    # Container readiness gate must still fire in forward().
    assert call.container_slot_name == "rerank"


@pytest.mark.asyncio
async def test_other_paths_not_rewritten() -> None:
    """/v1/chat/completions and /v1/audio/speech paths are NOT rewritten.

    Only /v1/rerankings has an entry in _UPSTREAM_PATH_REWRITES.
    """
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    tts = make_slot("tts", "http://127.0.0.1:8084/v1")
    upstreams = FakeUpstreamRegistry([chat, tts])
    models = FakeModelRegistry(routes={})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        cached_models=lambda name: ["gpt-mock"] if name == "chat" else [],
    )

    # Chat path — must be /v1/chat/completions, not rewritten
    call = await dispatcher.dispatch(
        make_request(path="/v1/chat/completions"),
        body={"model": "gpt-mock", "messages": []},
    )
    assert "/chat/completions" in call.target_url
    assert "/rerank" not in call.target_url

    # TTS path — must be /v1/audio/speech, not rewritten
    call2 = await dispatcher.dispatch(
        make_request(path="/v1/audio/speech"),
        body={"model": "kokoro", "input": "hi", "voice": "af_bella"},
    )
    assert "/audio/speech" in call2.target_url
    assert "/rerank" not in call2.target_url


# ── fallback / no-slot case ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_rerank_slot_falls_back_to_no_route_found() -> None:
    """No rerank upstream registered → LegacyResolutionFailed → NoRouteFound.

    Pre-migration behaviour preserved: if there is no rerank slot, requests
    to /v1/rerankings do not silently fall to embed or chat — they get a
    typed 404 so the caller knows routing failed cleanly.
    """
    # Only embed registered — no rerank slot.
    embed = make_slot("embed", "http://127.0.0.1:8086/v1")
    upstreams = FakeUpstreamRegistry([embed])
    models = FakeModelRegistry(routes={})

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)

    with pytest.raises(NoRouteFound) as exc:
        await dispatcher.dispatch(
            make_request(path="/v1/rerankings"),
            body={"model": "bge-reranker-v2-m3", "query": "foo", "documents": ["bar"]},
        )
    assert exc.value.code == "dispatch.no_route"
    assert isinstance(exc.value.__cause__, LegacyResolutionFailed)


# ── container upstream path-pin acceptance ────────────────────────────────────


def test_proxy_rerankings_path_pin_resolves_container_remote() -> None:
    """resolve_by_capability: /v1/rerankings + rerank registered kind=remote → returned.

    Container slots register as kind='remote' with slot_name set.  The
    path_pinned acceptance gate must accept them (same as tts/embed containers).
    """
    container_rerank = make_remote_rerank(port=8083)
    upstreams = FakeUpstreamRegistry([container_rerank])

    upstream = resolve_by_capability(
        path="/v1/rerankings",
        body={"model": "bge-reranker-v2-m3", "query": "foo", "documents": ["bar"]},
        upstreams=upstreams,
    )
    assert upstream.name == "rerank"
    assert upstream.kind == "remote"
