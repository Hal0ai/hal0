"""Unit tests for ``hal0.dispatcher.flm_trio`` — PR-19.

The trio router is purely two methods + one discovery helper, so the
test surface is:

  * ``find_flm_chat_backend_url`` returns the URL when a ``recipe=flm``
    AND ``type=llm`` entry is in ``loaded[]``, and only then.
  * Both keys ``loaded`` and ``all_models_loaded`` are accepted (Lemonade
    has used both depending on version).
  * Non-FLM entries (llamacpp/whispercpp) are skipped even if loaded.
  * Non-LLM FLM entries (defensively — should not happen today) are
    skipped.
  * Missing / empty / non-string ``backend_url`` returns ``None``.
  * Lemonade errors are swallowed (returns ``None``) — never raises out.
  * ``dispatch_stt_npu`` posts the raw bytes to ``<backend>/v1/audio/transcriptions``.
  * ``dispatch_embed_npu`` posts the JSON body to ``<backend>/v1/embeddings``.
  * Both raise :class:`FLMTrioNotAvailable` with the plan §5.3 message
    when no FLM chat is loaded.
  * Param forwarding (extra fields in embed body, multipart bytes for
    STT) preserved verbatim.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.dispatcher.flm_trio import FLMTrioNotAvailable, FLMTrioRouter
from hal0.lemonade.client import LemonadeClient
from hal0.lemonade.errors import (
    LemonadeHTTPError,
    LemonadeUnavailableError,
)

# ── Helpers ─────────────────────────────────────────────────────────────


def _mock_transport(handler: Any) -> httpx.AsyncClient:
    """httpx ``MockTransport`` convenience wrapper."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def _lemonade_with_health(payload: dict[str, Any]) -> tuple[LemonadeClient, httpx.AsyncClient]:
    """Build a LemonadeClient whose ``/v1/health`` returns ``payload``.

    Returns both so the caller can ``aclose()`` the transport.
    """

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"detail": "unexpected path"})

    transport = _mock_transport(h)
    return LemonadeClient(http_client=transport), transport


# ── find_flm_chat_backend_url ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_backend_url_returns_url_when_flm_chat_loaded() -> None:
    """The canonical happy path — one FLM LLM entry yields its backend_url."""
    payload = {
        "loaded": [
            {
                "model_name": "gemma3:1b",
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_nothing_loaded() -> None:
    client, transport = _lemonade_with_health({"loaded": []})
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_skips_non_flm_entries() -> None:
    """A llamacpp LLM loaded on the iGPU must NOT be mistaken for an FLM."""
    payload = {
        "loaded": [
            {
                "model_name": "hermes-4-14b",
                "recipe": "llamacpp",
                "type": "llm",
                "backend_url": "http://127.0.0.1:9101",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_skips_non_llm_flm_entries() -> None:
    """Defensive — should not happen today but if Lemonade ever advertises
    an FLM-typed embed entry directly, we must NOT pick it up as the
    chat anchor (it isn't multiplex-capable on its own)."""
    payload = {
        "loaded": [
            {
                "model_name": "embed-gemma",
                "recipe": "flm",
                "type": "embedding",
                "backend_url": "http://127.0.0.1:14003",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_accepts_all_models_loaded_alias() -> None:
    """Forward-compat — Lemonade has emitted both ``loaded`` and
    ``all_models_loaded`` depending on version. We accept either."""
    payload = {
        "all_models_loaded": [
            {
                "model_name": "gemma3:1b",
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


@pytest.mark.asyncio
async def test_find_backend_url_picks_first_match_with_both_keys_present() -> None:
    """When both keys exist (unlikely but defensible), the canonical
    ``loaded`` is consulted first."""
    payload = {
        "loaded": [
            {
                "model_name": "gemma3:1b",
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ],
        "all_models_loaded": [
            {
                "model_name": "qwen3:0.6b",
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14999",
            }
        ],
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


@pytest.mark.asyncio
async def test_find_backend_url_strips_trailing_slash() -> None:
    """Defensive — Lemonade typically emits bare URLs but we tolerate
    trailing slashes so the ``{backend_url}/v1/...`` join never doubles up."""
    payload = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002/",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


@pytest.mark.asyncio
async def test_find_backend_url_strips_trailing_v1_suffix() -> None:
    """Regression: lemond 10.6.0 reports ``backend_url`` WITH a ``/v1``
    suffix (e.g. ``http://127.0.0.1:8001/v1``). The dispatch methods join
    ``{backend_url}/v1/...``, so an unstripped suffix produced
    ``/v1/v1/embeddings`` → 404. Normalise the discovered base to drop a
    trailing ``/v1`` (and any trailing slash) so the join yields exactly
    one ``/v1``."""
    payload = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:8001/v1",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8001"


@pytest.mark.asyncio
async def test_find_backend_url_strips_trailing_v1_with_slash() -> None:
    """``/v1/`` (suffix + trailing slash) normalises the same way."""
    payload = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:8001/v1/",
            }
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:8001"


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_backend_url_missing() -> None:
    payload = {
        "loaded": [{"recipe": "flm", "type": "llm"}],
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_backend_url_empty_string() -> None:
    payload = {
        "loaded": [{"recipe": "flm", "type": "llm", "backend_url": "   "}],
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_backend_url_not_string() -> None:
    payload = {
        "loaded": [{"recipe": "flm", "type": "llm", "backend_url": 14002}],
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_lemonade_unavailable() -> None:
    """Health probe raising LemonadeUnavailableError → None (no exception)."""

    def h(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_health_returns_5xx() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client)
        # Confirm via direct call that this would raise LemonadeHTTPError —
        # the router has to swallow it.
        with pytest.raises(LemonadeHTTPError):
            await client.health()
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_body_is_not_dict() -> None:
    def h(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_returns_none_when_loaded_is_not_list() -> None:
    payload = {"loaded": "not-a-list"}
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_find_backend_url_skips_non_dict_entries_in_loaded() -> None:
    payload = {
        "loaded": [
            "not-a-dict",
            None,
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            },
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


# ── dispatch_stt_npu ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_stt_posts_multipart_to_flm_child() -> None:
    """Verify the URL + content-type + body bytes the FLM child sees."""
    health = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=health)
        return httpx.Response(200, json={"text": "transcribed"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client, http_client=transport)
        multipart_body = (
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="model"\r\n\r\n'
            b"whisper-v3\r\n"
            b"--boundary\r\n"
            b'Content-Disposition: form-data; name="file"; filename="x.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\n"
            b"RIFF\x00\x00\x00\x00WAVE\r\n"
            b"--boundary--\r\n"
        )
        resp = await router.dispatch_stt_npu(
            body=multipart_body,
            content_type="multipart/form-data; boundary=boundary",
        )

    assert resp.status_code == 200
    # The FLM-child URL is what we computed from /v1/health, not Lemonade's
    # base URL. The path is the OpenAI-shape transcription endpoint.
    assert captured["url"] == "http://127.0.0.1:14002/v1/audio/transcriptions"
    assert captured["method"] == "POST"
    # Multipart boundary preserved — without this, the FLM-side parser
    # fails and the request 415s.
    assert captured["content_type"].startswith("multipart/form-data")
    assert "boundary=boundary" in captured["content_type"]
    # Body bytes round-trip verbatim — the wav payload must not be
    # re-encoded along the way.
    assert captured["body"] == multipart_body


@pytest.mark.asyncio
async def test_dispatch_stt_raises_flm_trio_unavailable_when_no_chat_loaded() -> None:
    """Plan §5.3 — the surface error has to call out the user action."""
    client, transport = _lemonade_with_health({"loaded": []})
    async with transport:
        router = FLMTrioRouter(client)
        with pytest.raises(FLMTrioNotAvailable) as exc:
            await router.dispatch_stt_npu(
                body=b"--x--\r\n",
                content_type="multipart/form-data; boundary=x",
            )
    assert "load an NPU chat slot first" in str(exc.value)
    # Carries the route in details so the dashboard can show context.
    assert exc.value.details.get("endpoint") == "/v1/audio/transcriptions"
    # Stable code so frontends can pattern-match.
    assert exc.value.code == "npu.trio_unavailable"
    assert exc.value.status == 503


@pytest.mark.asyncio
async def test_dispatch_stt_raises_when_only_llamacpp_loaded() -> None:
    """A non-FLM LLM in loaded[] is NOT the trio chat anchor."""
    health = {
        "loaded": [
            {
                "recipe": "llamacpp",
                "type": "llm",
                "backend_url": "http://127.0.0.1:9101",
            }
        ]
    }
    client, transport = _lemonade_with_health(health)
    async with transport:
        router = FLMTrioRouter(client)
        with pytest.raises(FLMTrioNotAvailable):
            await router.dispatch_stt_npu(
                body=b"--x--\r\n",
                content_type="multipart/form-data; boundary=x",
            )


# ── dispatch_embed_npu ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_embed_posts_json_to_flm_child() -> None:
    health = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["content_type"] = req.headers.get("content-type", "")
        captured["body"] = req.content
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=health)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3]}], "model": "embed-gemma"},
        )

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client, http_client=transport)
        resp = await router.dispatch_embed_npu(
            body={"model": "embed-gemma", "input": "hello world"},
        )

    assert resp.status_code == 200
    # URL points at the FLM child, NOT lemond.
    assert captured["url"] == "http://127.0.0.1:14002/v1/embeddings"
    assert captured["method"] == "POST"
    assert captured["content_type"].startswith("application/json")
    import json as _json

    decoded = _json.loads(captured["body"])
    assert decoded == {"model": "embed-gemma", "input": "hello world"}


@pytest.mark.asyncio
async def test_dispatch_embed_preserves_extra_params() -> None:
    """Caller-supplied params (encoding_format, dimensions, ...) round-trip
    untouched — the router is pure forward, no shape opinions."""
    health = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=health)
        captured["body"] = req.content
        return httpx.Response(200, json={"data": []})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client, http_client=transport)
        body = {
            "model": "embed-gemma",
            "input": ["one", "two"],
            "encoding_format": "float",
            "dimensions": 768,
            "user": "test-user-123",
        }
        await router.dispatch_embed_npu(body=body)

    import json as _json

    decoded = _json.loads(captured["body"])
    assert decoded == body


@pytest.mark.asyncio
async def test_dispatch_embed_raises_flm_trio_unavailable() -> None:
    client, transport = _lemonade_with_health({"loaded": []})
    async with transport:
        router = FLMTrioRouter(client)
        with pytest.raises(FLMTrioNotAvailable) as exc:
            await router.dispatch_embed_npu(body={"model": "embed-gemma", "input": "x"})
    assert "load an NPU chat slot first" in str(exc.value)
    assert exc.value.details.get("endpoint") == "/v1/embeddings"


@pytest.mark.asyncio
async def test_dispatch_embed_propagates_upstream_status_codes() -> None:
    """An FLM-side 4xx (e.g. validation) reaches the caller verbatim."""
    health = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=health)
        return httpx.Response(422, json={"detail": "empty input"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client, http_client=transport)
        resp = await router.dispatch_embed_npu(body={"model": "embed-gemma", "input": ""})

    assert resp.status_code == 422
    assert resp.json() == {"detail": "empty input"}


# ── content-type isolation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_stt_extra_headers_dont_clobber_content_type() -> None:
    """If the caller passes an ``extra_headers`` dict that itself contains
    a content-type, the multipart boundary that the route handler computed
    MUST win — otherwise the FLM-side multipart parser breaks."""
    health = {
        "loaded": [
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            }
        ]
    }
    captured: dict[str, Any] = {}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json=health)
        captured["content_type"] = req.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "ok"})

    async with _mock_transport(h) as transport:
        client = LemonadeClient(http_client=transport)
        router = FLMTrioRouter(client, http_client=transport)
        await router.dispatch_stt_npu(
            body=b"--x--\r\n",
            content_type="multipart/form-data; boundary=correct",
            extra_headers={"Content-Type": "application/json"},  # adversarial
        )

    assert "boundary=correct" in captured["content_type"]


# ── sanity: the router doesn't crash on missing recipe/type fields ──────


@pytest.mark.asyncio
async def test_find_backend_url_handles_entries_without_recipe_field() -> None:
    """Old-snapshot entries that predate the recipe tagging are safely
    skipped (no AttributeError / KeyError)."""
    payload = {
        "loaded": [
            {"model_name": "legacy", "backend_url": "http://127.0.0.1:99"},
            {
                "recipe": "flm",
                "type": "llm",
                "backend_url": "http://127.0.0.1:14002",
            },
        ]
    }
    client, transport = _lemonade_with_health(payload)
    async with transport:
        router = FLMTrioRouter(client)
        assert await router.find_flm_chat_backend_url() == "http://127.0.0.1:14002"


@pytest.mark.asyncio
async def test_swallows_unexpected_lemonade_error_types() -> None:
    """Defence-in-depth — a bug in LemonadeClient that raises a non-typed
    error must NOT escape the router (the contract is "never raises out
    of find_flm_chat_backend_url"). We simulate by using a client whose
    .health() raises a generic Exception."""

    class _BrokenClient:
        async def health(self) -> dict[str, Any]:
            raise RuntimeError("boom")

    # Type-cheat: FLMTrioRouter accepts the LemonadeClient class but
    # duck-types .health() at the call site, so a stand-in works.
    router = FLMTrioRouter(_BrokenClient())  # type: ignore[arg-type]
    assert await router.find_flm_chat_backend_url() is None


@pytest.mark.asyncio
async def test_known_lemonade_error_subclass_returns_none() -> None:
    """Smoke check that the typed LemonadeError path also returns None."""

    class _OfflineClient:
        async def health(self) -> dict[str, Any]:
            raise LemonadeUnavailableError("daemon down")

    router = FLMTrioRouter(_OfflineClient())  # type: ignore[arg-type]
    assert await router.find_flm_chat_backend_url() is None
