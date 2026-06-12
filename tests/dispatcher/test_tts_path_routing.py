"""Tests for TTS path-based routing to the ``tts`` slot.

Phase B task B4 — ``/audio/speech`` path-routes to the ``tts`` slot at
both the router (_default_for_path) and proxy (legacy fallback Rule 2)
layers.  Model-id matching is intentionally bypassed because the kokoro
container advertises ``"kokoro"`` while callers send ``"kokoro-v1"``,
``"tts"``, etc.

Mirrors the existing embed/rerank test style in test_router.py.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from hal0.dispatcher.proxy import LegacyResolutionFailed, resolve_slot
from hal0.dispatcher.router import Dispatcher, UpstreamCall
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── test doubles (same pattern as test_router.py) ────────────────────────────


class FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store: dict[str, Upstream] = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)

    def __getitem__(self, name: str) -> Upstream:
        # Dispatcher Step 1 indexes the registry for a known-present name.
        return self._store[name]


class FakeModelRegistry:
    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = routes or {}

    def route_for(self, model_id: str) -> str | None:
        return self._routes.get(model_id)


def make_request(path: str = "/v1/audio/speech", method: str = "POST") -> Request:
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


def make_slot(name: str, url: str = "http://127.0.0.1:8084/v1") -> Upstream:
    return Upstream(name=name, kind="slot", url=url, slot_name=name)


def make_remote_tts(port: int = 8084) -> Upstream:
    """A kind='remote' container upstream registered as 'tts'."""
    return Upstream(
        name="tts",
        kind="remote",
        url=f"http://127.0.0.1:{port}/v1",
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
        slot_name="tts",  # container-backed remote
    )


# ── router._default_for_path ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_speech_uses_tts_default_from_path() -> None:
    """No model in body + /audio/speech path → _default_for_path returns 'tts'.

    Uses a registry binding for 'tts' so the router's registry path resolves
    cleanly, letting us assert the resolution_path rather than catching
    LegacyResolutionFailed.
    """
    tts = make_slot("tts", "http://127.0.0.1:8084/v1")
    upstreams = FakeUpstreamRegistry([tts])
    models = FakeModelRegistry(routes={"tts": "tts"})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        cached_models=lambda name: ["tts"] if name == "tts" else [],
    )

    call = await dispatcher.dispatch(
        make_request(path="/v1/audio/speech"),
        body={"input": "hello", "voice": "af_bella"},  # no model key
    )
    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "tts"
    assert call.resolution_path == "registry"


@pytest.mark.asyncio
async def test_audio_speech_kokoro_v1_reaches_tts_slot() -> None:
    """model='kokoro-v1' body + /audio/speech → tts upstream.

    The model id 'kokoro-v1' has no registry binding, so the request falls
    to _default_for_path which uses the path to select 'tts'.
    """
    tts = make_slot("tts", "http://127.0.0.1:8084/v1")
    upstreams = FakeUpstreamRegistry([tts])
    models = FakeModelRegistry(routes={})  # no registry binding for 'kokoro-v1'

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
    )

    call = await dispatcher.dispatch(
        make_request(path="/v1/audio/speech"),
        body={"model": "kokoro-v1", "input": "hello", "voice": "af_bella"},
    )
    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "tts"
    assert call.resolution_path == "legacy_slot:tts"


# ── proxy.resolve_slot (legacy fallback) ─────────────────────────────────────


def test_proxy_audio_speech_path_pins_to_tts() -> None:
    """Legacy fallback Rule 2: /audio/speech in path → tts slot."""
    tts = make_slot("tts")
    upstreams = FakeUpstreamRegistry([tts])

    upstream = resolve_slot(
        path="/v1/audio/speech",
        body={"model": "kokoro-v1", "input": "hi", "voice": "af_bella"},
        upstreams=upstreams,
    )
    assert upstream.name == "tts"


def test_proxy_audio_speech_no_model_pins_to_tts() -> None:
    """path-based pin fires even when body has no model field."""
    tts = make_slot("tts")
    upstreams = FakeUpstreamRegistry([tts])

    upstream = resolve_slot(
        path="/v1/audio/speech",
        body={"input": "hi", "voice": "af_bella"},
        upstreams=upstreams,
    )
    assert upstream.name == "tts"


def test_proxy_audio_speech_unknown_model_still_pins_to_tts() -> None:
    """Any model id on /audio/speech routes to tts (not chat)."""
    tts = make_slot("tts")
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    upstreams = FakeUpstreamRegistry([tts, chat])

    upstream = resolve_slot(
        path="/v1/audio/speech",
        body={"model": "some-unknown-tts-model", "input": "hello", "voice": "af_bella"},
        upstreams=upstreams,
    )
    assert upstream.name == "tts"


def test_proxy_audio_speech_missing_tts_slot_raises_typed_error() -> None:
    """If tts slot not registered, legacy fallback raises LegacyResolutionFailed."""
    chat = make_slot("chat")
    upstreams = FakeUpstreamRegistry([chat])  # no tts

    with pytest.raises(LegacyResolutionFailed) as exc:
        resolve_slot(
            path="/v1/audio/speech",
            body={"model": "kokoro-v1", "input": "hi", "voice": "af_bella"},
            upstreams=upstreams,
        )
    assert exc.value.code == "dispatch.legacy_unresolved"
    assert "tts" in str(exc.value)


# ── container upstream preemption ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_container_tts_upstream_preempts_registry() -> None:
    """A tts container remote (kind=remote, slot_name=tts) wins Step 0 preemption.

    The tts slot registers as kind='remote' (container), not kind='slot'
    (local slot). Step 0 in dispatch scans container remotes first — if the
    model id is in the cached_models for that upstream it wins immediately,
    before registry or legacy paths run.
    """
    container_tts = make_remote_tts(port=8084)
    upstreams = FakeUpstreamRegistry([container_tts])
    models = FakeModelRegistry(routes={})

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        # Simulate tts container advertising "kokoro" as its model id.
        cached_models=lambda name: ["kokoro"] if name == "tts" else [],
    )

    call = await dispatcher.dispatch(
        make_request(path="/v1/audio/speech"),
        body={"model": "kokoro", "input": "hello", "voice": "af_bella"},
    )
    assert call.upstream_name == "tts"
    assert call.resolution_path == "container:tts"


# ── NEGATIVE: /audio/transcriptions NOT affected ──────────────────────────────


def test_proxy_audio_transcriptions_not_routed_to_tts() -> None:
    """/audio/transcriptions is STT — must NOT be pinned to the tts slot.

    The _TTS_PATHS check is '/audio/speech' only. Transcription requests
    fall through to the chat fallback (or caller-specified slot).
    """
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    upstreams = FakeUpstreamRegistry([chat])

    upstream = resolve_slot(
        path="/v1/audio/transcriptions",
        body={"model": "moonshine-small", "language": "en"},
        upstreams=upstreams,
    )
    # Falls to chat (Rule 8), NOT tts.
    assert upstream.name == "chat"


@pytest.mark.asyncio
async def test_router_audio_transcriptions_not_default_to_tts() -> None:
    """_default_for_path('/v1/audio/transcriptions') must not return 'tts'."""
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    upstreams = FakeUpstreamRegistry([chat])
    models = FakeModelRegistry(routes={})

    dispatcher = Dispatcher(upstream_registry=upstreams, model_registry=models)

    # dispatcher._default_for_path is accessible for a direct unit check.
    result = dispatcher._default_for_path("/v1/audio/transcriptions")
    assert result != "tts"
    assert result == "chat"


# ── C1: path-pin must resolve container-backed remote upstreams ───────────────
#
# Container slots register as kind="remote" (manager._register_container_upstream)
# with slot_name set. resolve_slot's old kind=="slot" gate rejected them, so
# kokoro-v1 requests raised LegacyResolutionFailed → NoRouteFound instead of
# reaching the live container-backed tts slot.


@pytest.mark.asyncio
async def test_dispatch_kokoro_v1_resolves_container_remote_tts() -> None:
    """Full dispatch(): model='kokoro-v1' + tts as kind=remote → tts remote wins.

    Step 0 preempt misses (container advertises 'kokoro', not 'kokoro-v1'),
    registry/passthrough miss, so the legacy path-pin MUST resolve the
    container-backed remote — never NoRouteFound.
    """
    container_tts = make_remote_tts(port=8084)
    upstreams = FakeUpstreamRegistry([container_tts])
    models = FakeModelRegistry(routes={})  # no registry binding

    async def online(_u: Upstream) -> bool:
        return True

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        is_online=online,
        # Container's /v1/models advertises 'kokoro' only — Step 0 misses.
        cached_models=lambda name: ["kokoro"] if name == "tts" else [],
    )

    call = await dispatcher.dispatch(
        make_request(path="/v1/audio/speech"),
        body={"model": "kokoro-v1", "input": "hello", "voice": "af_bella"},
    )
    assert isinstance(call, UpstreamCall)
    assert call.upstream_name == "tts"
    assert call.resolution_path == "legacy_slot:tts"
    assert call.target_url == "http://127.0.0.1:8084/v1/audio/speech"
    # Container readiness gate must still fire in forward().
    assert call.container_slot_name == "tts"


def test_proxy_tts_path_pin_resolves_container_remote() -> None:
    """resolve_slot: /audio/speech + tts registered kind=remote → returned."""
    container_tts = make_remote_tts(port=8084)
    upstreams = FakeUpstreamRegistry([container_tts])

    upstream = resolve_slot(
        path="/v1/audio/speech",
        body={"model": "kokoro-v1", "input": "hi", "voice": "af_bella"},
        upstreams=upstreams,
    )
    assert upstream.name == "tts"
    assert upstream.kind == "remote"


def test_proxy_embed_path_pin_resolves_container_remote() -> None:
    """Path-pin container acceptance applies to embed pins too.

    Phase C: /rerank path-pin now targets the dedicated ``rerank`` slot
    (not embed); see test_rerank_path_routing.py for those tests.
    """
    container_embed = Upstream(
        name="embed",
        kind="remote",
        url="http://127.0.0.1:8086/v1",
        auth_style="none",
        slot_name="embed",  # container-backed
    )
    upstreams = FakeUpstreamRegistry([container_embed])

    upstream = resolve_slot(
        path="/v1/embeddings",
        body={"input": "x"},
        upstreams=upstreams,
    )
    assert upstream.name == "embed"
    assert upstream.kind == "remote"


def test_proxy_genuine_remote_not_accepted_by_path_pin() -> None:
    """A genuine external remote (slot_name=None) named 'tts' is still rejected.

    slot_name is the container-backed marker (#656); a plain remote named
    'tts' is NOT a local slot and must not absorb path-pinned traffic.
    """
    genuine_remote = Upstream(
        name="tts",
        kind="remote",
        url="https://api.example.com/v1",
        auth_style="bearer",
        # slot_name unset — genuine remote
    )
    upstreams = FakeUpstreamRegistry([genuine_remote])

    with pytest.raises(LegacyResolutionFailed):
        resolve_slot(
            path="/v1/audio/speech",
            body={"model": "kokoro-v1", "input": "hi", "voice": "af_bella"},
            upstreams=upstreams,
        )


def test_proxy_model_name_rule_does_not_resolve_container_remote() -> None:
    """Container-remote acceptance is scoped to PATH pins only.

    Rule 7 (explicit slot-name addressing via model id) on a non-pinned
    path must NOT resolve a kind=remote upstream — model='tts' on
    /chat/completions falls through to the chat fallback as before.
    """
    container_tts = make_remote_tts(port=8084)
    chat = make_slot("chat", "http://127.0.0.1:8081/v1")
    upstreams = FakeUpstreamRegistry([container_tts, chat])

    upstream = resolve_slot(
        path="/v1/chat/completions",
        body={"model": "tts", "messages": []},
        upstreams=upstreams,
    )
    assert upstream.name == "chat"
