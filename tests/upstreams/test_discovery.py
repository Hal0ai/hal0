"""Tests for hal0.upstreams.discovery and UpstreamRegistry.fetch_model_payload."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.upstreams.discovery import (
    DISCOVERY_TTL_S,
    DiscoveryCache,
    guess_type,
    parse_models_payload,
)
from hal0.upstreams.presets import Preset, PresetModel
from hal0.upstreams.registry import Upstream, UpstreamRegistry


def _preset(*models: PresetModel) -> Preset:
    return Preset(
        id="p",
        name="P",
        category="cloud",
        base_url="https://example.invalid/v1",
        auth="none",
        models=models,
    )


def test_parse_openai_shape() -> None:
    out = parse_models_payload({"data": [{"id": "a"}, {"id": "text-embedding-3-small"}]}, None)
    assert [(m.id, m.type, m.type_source) for m in out] == [
        ("a", "chat", "guess"),
        ("text-embedding-3-small", "embed", "guess"),
    ]


def test_parse_openrouter_prices_and_context() -> None:
    body = {
        "data": [
            {
                "id": "qwen/qwen3-coder",
                "context_length": 262144,
                "pricing": {"prompt": "0.00000022", "completion": "0.00000095"},
            }
        ]
    }
    m = parse_models_payload(body, None)[0]
    assert (m.context, m.price_in, m.price_out) == (
        262144,
        pytest.approx(0.22),
        pytest.approx(0.95),
    )


def test_preset_fills_type_context_and_recommended() -> None:
    preset = _preset(PresetModel("speech-x", "tts", context=1000, recommended=True))
    m = parse_models_payload({"data": [{"id": "speech-x"}]}, preset)[0]
    assert (m.type, m.type_source, m.recommended, m.context) == ("tts", "preset", True, 1000)


def test_provider_fields_beat_preset() -> None:
    preset = _preset(PresetModel("m", "chat", context=10, price_in=1.0, price_out=2.0))
    body = {"data": [{"id": "m", "context_length": 99, "pricing": {"prompt": "0.000003"}}]}
    m = parse_models_payload(body, preset)[0]
    assert m.context == 99
    assert m.price_in == pytest.approx(3.0)
    assert m.price_out == 2.0


@pytest.mark.parametrize(
    "body",
    [["a", "b"], {"models": []}, {}, "<html>", None, {"data": "nope"}, {"data": [{"no_id": 1}, 7]}],
)
def test_odd_payloads_never_raise(body: Any) -> None:
    out = parse_models_payload(body, None)
    assert isinstance(out, list)
    assert [m.id for m in out] == (["a", "b"] if body == ["a", "b"] else [])


def test_duplicate_ids_first_wins() -> None:
    body = {"data": [{"id": "x", "context_length": 1}, {"id": "x", "context_length": 2}, "x"]}
    out = parse_models_payload(body, None)
    assert [(m.id, m.context) for m in out] == [("x", 1)]


@pytest.mark.parametrize("price", ["free", "", None, {}, "nan", "-1"])
def test_bad_price_is_ignored(price: Any) -> None:
    body = {"data": [{"id": "m", "pricing": {"prompt": price, "completion": price}}]}
    m = parse_models_payload(body, None)[0]
    assert (m.price_in, m.price_out) == (None, None)


def test_bad_context_is_ignored() -> None:
    body = {"data": [{"id": "m", "context_length": "big"}, {"id": "n", "context_length": True}]}
    assert [m.context for m in parse_models_payload(body, None)] == [None, None]


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("BAAI/bge-reranker-v2", "rerank"),
        ("text-embedding-3-small", "embed"),
        ("bge-m3", "embed"),
        ("intfloat/e5-large", "embed"),
        ("whisper-large-v3", "stt"),
        ("gpt-4o-transcribe", "stt"),
        ("tts-1", "tts"),
        ("gpt-4o-mini-tts", "tts"),
        ("voice-clone", "tts"),
        ("dall-e-3", "image"),
        ("flux-pro", "image"),
        ("gpt-image-1", "image"),
        ("gpt-4o", "chat"),
    ],
)
def test_guess_type(model_id: str, expected: str) -> None:
    assert guess_type(model_id) == expected


def test_cache_staleness() -> None:
    c = DiscoveryCache()
    assert c.is_stale("x")
    c.put("x", [], now=1000.0)
    assert not c.is_stale("x", now=1000.0 + 21599)
    assert c.is_stale("x", now=1000.0 + 21601)
    assert DISCOVERY_TTL_S == 21600


def test_cache_get_and_drop() -> None:
    c = DiscoveryCache()
    assert c.get("x") is None
    c.put("x", [], now=5.0)
    assert c.get("x") == ([], 5.0)
    c.drop("x")
    assert c.get("x") is None
    c.drop("x")  # absent is fine


# ── registry.fetch_model_payload ──────────────────────────────────────────────


def _registry(handler: Any) -> UpstreamRegistry:
    reg = UpstreamRegistry()
    reg.add(Upstream(name="r", kind="remote", url="https://example.invalid/v1", auth_style="none"))
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    reg._get_client = lambda: client  # type: ignore[method-assign]
    return reg


async def test_payload_returns_decoded_body() -> None:
    reg = _registry(lambda req: httpx.Response(200, json={"data": [{"id": "a"}]}))
    assert await reg.fetch_model_payload("r") == {"data": [{"id": "a"}]}
    assert await reg.fetch_models("r") == ["a"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b""),
        httpx.Response(200, content=b"<html>"),
        httpx.Response(500, json={"data": []}),
        httpx.Response(401, json={}),
    ],
)
async def test_payload_none_on_failure(response: httpx.Response) -> None:
    reg = _registry(lambda req: response)
    assert await reg.fetch_model_payload("r") is None
    assert await reg.fetch_models("r") == []


async def test_payload_none_on_transport_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert await _registry(boom).fetch_model_payload("r") is None


async def test_payload_none_for_unknown_upstream() -> None:
    assert await UpstreamRegistry().fetch_model_payload("nope") is None
