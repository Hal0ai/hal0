"""Wiring tests for ``/v1/audio/*`` envelope semantics.

Covers two harness findings:

  * **#14** — STT pipeline must not leak the moonshine container's ffmpeg
    argv / ``CalledProcessError`` repr through ``POST /v1/audio/transcriptions``
    when the upload isn't a decodable audio format. The proxy scrubs any
    upstream body carrying those markers and re-emits a clean 415 with the
    hal0 envelope ``code="audio.unsupported_format"``.
  * **#18** — ``POST /v1/audio/speech`` with the OpenAI body missing the
    ``model`` field must return 400 with ``code="request.missing_model"``,
    not the dispatcher's misleading 404 from the default-model fallback
    path.

Happy-path STT + TTS round-trips use ``httpx.MockTransport`` injected into
the dispatcher's client so we exercise the full route → dispatcher →
forward path without a live upstream container.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.upstreams.registry import Upstream

# ── Helpers ───────────────────────────────────────────────────────────────────


def _seed_stt_upstream(client: TestClient, port: int = 8089) -> None:
    """Register a fake STT slot the dispatcher's legacy fallback will land on.

    The legacy heuristics in ``hal0.dispatcher.proxy`` don't have a rule for
    ``/v1/audio/transcriptions`` model ids that aren't FLM tag-style, so an
    arbitrary STT model name falls through to the ``primary`` slot. We
    register under that name so the dispatch resolves cleanly in tests
    without having to install a registry binding.
    """
    client.app.state.upstreams.upsert(
        Upstream(
            name="primary",
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name="primary",
            auth_style="none",
        )
    )


def _seed_tts_upstream(client: TestClient, port: int = 8090) -> None:
    """Register a fake TTS slot the dispatcher's legacy fallback will land on.

    Same fallthrough logic as STT — the dispatcher routes unknown ``model``
    ids to ``primary``, so we register there.
    """
    client.app.state.upstreams.upsert(
        Upstream(
            name="primary",
            kind="slot",
            url=f"http://127.0.0.1:{port}/v1",
            slot_name="primary",
            auth_style="none",
        )
    )


def _install_mock_transport(client: TestClient, handler: httpx.MockTransport | object) -> None:
    """Swap the dispatcher's httpx client for one backed by ``handler``.

    The dispatcher lazily constructs its own client on first use, so we
    install ours BEFORE any /v1 request — once the lifespan has finished
    (which the ``client`` fixture guarantees) but before the test fires.
    """
    if not isinstance(handler, httpx.MockTransport):
        handler = httpx.MockTransport(handler)  # type: ignore[arg-type]
    dispatcher = client.app.state.dispatcher
    # Close any existing client first so we don't leak sockets.
    if getattr(dispatcher, "_http_client", None) is not None:
        # Best-effort sync close — the test client's event loop is paused
        # between requests so a fire-and-forget close is acceptable here.
        try:
            import asyncio

            asyncio.get_event_loop().run_until_complete(dispatcher.aclose())
        except Exception:
            pass
    dispatcher._http_client = httpx.AsyncClient(transport=handler)
    dispatcher._owns_http_client = True


# ── #14: STT envelope on non-audio uploads ────────────────────────────────────


def test_v1_audio_transcriptions_redacts_ffmpeg_argv(client: TestClient) -> None:
    """A non-audio multipart body must surface as 415 audio.unsupported_format.

    Simulates an older / out-of-tree moonshine container that hasn't been
    updated to redact its ``CalledProcessError`` — the upstream returns a
    leaky 500 body, and the proxy must scrub it so the client never sees
    the subprocess argv or the user-supplied tempfile path.
    """
    _seed_stt_upstream(client)

    leaky_body = (
        b"{\"detail\":\"Command '[\\'ffmpeg\\', \\'-y\\', \\'-i\\', "
        b"'/tmp/tmpABC.bin']' returned non-zero exit status 1.\"}"
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content=leaky_body,
            headers={"content-type": "application/json"},
        )

    _install_mock_transport(client, handler)

    files = {"file": ("not-audio.wav", b"this is not actually audio", "audio/wav")}
    data = {"model": "moonshine-small-streaming-en"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 415, r.text
    body_text = r.text
    body_lower = body_text.lower()
    # Acceptance per harness #14: no subprocess argv leaks through the proxy.
    assert "ffmpeg" not in body_lower, body_text
    assert "calledprocesserror" not in body_lower, body_text
    # And no tempfile path either (defence in depth — covered by the marker
    # check, but we assert explicitly so a future regex regression breaks
    # here rather than silently passing).
    assert "/tmp/" not in body_text, body_text

    payload = r.json()
    assert payload["error"]["code"] == "audio.unsupported_format"
    assert "unsupported audio format" in payload["error"]["message"].lower()


def test_v1_audio_transcriptions_clean_upstream_error_passes_through(
    client: TestClient,
) -> None:
    """An upstream 5xx body that doesn't mention ffmpeg passes through verbatim.

    Regression guard for the scrubber's narrowness — we only replace the body
    when the leak markers are present. Unrelated upstream failures should
    still surface their original envelope so callers can debug them.
    """
    _seed_stt_upstream(client)

    clean_body = b'{"detail":"model not loaded"}'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            content=clean_body,
            headers={"content-type": "application/json"},
        )

    _install_mock_transport(client, handler)

    files = {"file": ("audio.wav", b"RIFF" + b"\x00" * 1024, "audio/wav")}
    data = {"model": "moonshine-small-streaming-en"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "model not loaded"


# ── #18: TTS missing-model envelope ───────────────────────────────────────────


def test_v1_audio_speech_missing_model_returns_400_envelope(client: TestClient) -> None:
    """POST /v1/audio/speech without ``model`` → 400 request.missing_model.

    The dispatcher's no-route branch used to return 404 ('dispatch.no_route')
    when no model was supplied — misleading because the route IS mounted, the
    request just lacked a required field. Harness finding #18.
    """
    r = client.post(
        "/v1/audio/speech",
        json={"input": "hello world", "voice": "af_bella"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "request.missing_model"
    assert "model" in body["error"]["message"].lower()
    # Defence in depth: the dispatcher's 404 envelope must not slip through.
    assert body["error"]["code"] != "dispatch.no_route"


def test_v1_audio_speech_empty_model_returns_400_envelope(client: TestClient) -> None:
    """Whitespace-only ``model`` is treated as missing."""
    r = client.post(
        "/v1/audio/speech",
        json={"model": "   ", "input": "hi", "voice": "af_bella"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "request.missing_model"


# ── Happy paths (mocked upstream) ─────────────────────────────────────────────


def test_v1_audio_transcriptions_happy_path(client: TestClient) -> None:
    """A well-formed multipart STT request reaches the upstream and returns its body."""
    _seed_stt_upstream(client)

    expected = {"text": "hello world"}

    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        return httpx.Response(200, json=expected)

    _install_mock_transport(client, handler)

    files = {"file": ("clip.wav", b"RIFF" + b"\x00" * 64, "audio/wav")}
    data = {"model": "moonshine-small-streaming-en"}
    r = client.post("/v1/audio/transcriptions", files=files, data=data)

    assert r.status_code == 200, r.text
    assert r.json() == expected
    # The route forwards multipart bytes verbatim — content-type must
    # preserve the multipart boundary or the upstream's parser would fail.
    assert captured["content_type"].startswith("multipart/form-data")
    # And the multipart body must reach the upstream un-rewritten (the
    # request bytes carry the model field on the wire).
    assert b'name="model"' in captured["body"]  # type: ignore[operator]


def test_v1_audio_speech_happy_path(client: TestClient) -> None:
    """A well-formed TTS request streams the upstream's audio bytes back."""
    _seed_tts_upstream(client)

    fake_wav = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=fake_wav,
            headers={"content-type": "audio/wav"},
        )

    _install_mock_transport(client, handler)

    r = client.post(
        "/v1/audio/speech",
        json={"model": "tts", "input": "hello", "voice": "af_bella"},
    )

    assert r.status_code == 200, r.text
    assert r.content == fake_wav
    assert r.headers["content-type"].startswith("audio/wav")


# ── Sanity: the scrubber leaves non-audio routes alone ────────────────────────


@pytest.mark.parametrize(
    "path,body",
    [
        ("/v1/chat/completions", {"model": "primary", "messages": []}),
    ],
)
def test_scrubber_does_not_touch_non_audio_routes(
    client: TestClient, path: str, body: dict[str, object]
) -> None:
    """The audio leakage scrubber is scoped to ``/v1/audio/transcriptions``.

    A 500 body mentioning ffmpeg on a chat route (e.g. some upstream's stack
    trace) must pass through untouched — only the STT route applies the
    scrub. This protects callers that legitimately surface ffmpeg-adjacent
    text in non-audio responses.
    """
    client.app.state.upstreams.upsert(
        Upstream(
            name="primary",
            kind="slot",
            url="http://127.0.0.1:8081/v1",
            slot_name="primary",
            auth_style="none",
        )
    )

    leaky_body = b'{"detail":"trace mentions ffmpeg somewhere"}'

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=leaky_body)

    _install_mock_transport(client, handler)

    r = client.post(path, json=body)
    assert r.status_code == 500
    # Body unchanged — the chat route never touches the scrubber.
    assert "ffmpeg" in r.text
