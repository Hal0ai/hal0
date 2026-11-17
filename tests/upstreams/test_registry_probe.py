"""Unit tests for ``UpstreamRegistry.probe`` (pre-save provider check).

The probe handles a secret in transit: the key must never be stored, logged,
or echoed. HTTP is faked with ``httpx.MockTransport`` swapped in for the
registry's ``async_client`` factory.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hal0.upstreams import registry as registry_mod
from hal0.upstreams.registry import UpstreamRegistry

KEY = "sk-secret-123"
URL = "https://provider.example/v1"


def _wire(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    """Route the probe's client through ``handler``; return the seen requests."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        built = handler(request)
        if not built.is_stream_consumed:
            return built
        # Serve it as a live stream, like a real transport, so ``aiter_raw`` works.
        return httpx.Response(
            built.status_code, headers=built.headers, stream=httpx.ByteStream(built.content)
        )

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(wrapped)
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(registry_mod, "async_client", factory)
    return seen


async def test_probe_ok_returns_payload_and_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _wire(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            json={"data": [{"id": "m1"}]},
            headers={"x-ratelimit-remaining-requests": "499"},
        ),
    )
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is True
    assert out["status"] == 200
    assert out["payload"] == {"data": [{"id": "m1"}]}
    assert out["headers"]["x-ratelimit-remaining-requests"] == "499"
    assert out["error"] is None
    assert out["latency_ms"] >= 0
    assert str(seen[0].url) == f"{URL}/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


async def test_probe_401_returns_ok_false_with_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(401, text="nope"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert out["status"] == 401
    assert out["payload"] is None
    assert "401" in out["error"]


async def test_probe_timeout_returns_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"timed out talking to {request.url} with {KEY}")

    _wire(monkeypatch, boom)
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert out["status"] is None
    assert "timeout" in out["error"].lower()
    assert KEY not in repr(out)


async def test_probe_redacts_key_from_error_body_and_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(
        monkeypatch,
        lambda r: httpx.Response(
            401,
            text=f"bad credential Bearer {KEY} ({KEY})",
            headers={"x-echo": f"Bearer {KEY}"},
        ),
    )
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert KEY not in repr(out)
    assert "***" in out["error"]
    assert out["headers"]["x-echo"] == "Bearer ***"


async def test_probe_redacts_key_from_transport_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connect failed for {request.url} key={KEY}")

    _wire(monkeypatch, boom)
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert KEY not in repr(out)


async def test_probe_redacts_short_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(401, text="you sent abc"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key="abc")
    assert "abc" not in out["error"]
    assert "***" in out["error"]


async def test_probe_empty_key_sends_no_auth_and_redacts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(500, text="server broke"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key="")
    assert "authorization" not in seen[0].headers
    assert "server broke" in out["error"]
    assert "***" not in out["error"]


async def test_probe_auth_styles_build_expected_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    reg = UpstreamRegistry()
    await reg.probe(url=URL, auth_style="anthropic", key=KEY)
    await reg.probe(url=URL, auth_style="header", auth_header="X-Api-Key", key=KEY)
    await reg.probe(url=URL, auth_style="none", key=KEY)
    assert seen[0].headers["x-api-key"] == KEY
    assert seen[0].headers["anthropic-version"] == "2023-06-01"
    assert seen[1].headers["x-api-key"] == KEY
    assert "authorization" not in seen[2].headers
    assert KEY not in "".join(seen[2].headers.values())


async def test_probe_does_not_follow_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _wire(
        monkeypatch,
        lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/latest"}),
    )
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert len(seen) == 1
    assert out["ok"] is False
    assert out["status"] == 302


async def test_probe_caps_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    big = b"x" * (registry_mod.PROBE_MAX_BODY_BYTES + 1024)
    _wire(monkeypatch, lambda r: httpx.Response(200, content=big))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert out["payload"] is None
    assert "too large" in out["error"]


async def test_probe_non_json_200_is_not_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(200, text="<html>hi</html>"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert out["status"] == 200


@pytest.mark.parametrize("bad", ["ftp://x/v1", "file:///etc/passwd", "http://u:p@h/v1", "x", ""])
async def test_probe_rejects_bad_urls_without_a_request(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(ValueError) as info:
        await UpstreamRegistry().probe(url=bad, auth_style="bearer", key=KEY)
    assert seen == []
    assert KEY not in str(info.value)


async def test_probe_leaves_environment_and_registry_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    reg = UpstreamRegistry()
    before = dict(os.environ)
    await reg.probe(url=URL, auth_style="bearer", key=KEY)
    assert dict(os.environ) == before
    assert reg.list() == []


# ── review round: hardening ──────────────────────────────────────────────────


async def test_probe_bad_port_is_a_url_error_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(200, json={}))
    with pytest.raises(registry_mod.ProbeUrlError) as info:
        await UpstreamRegistry().probe(url="http://h:abc/v1", auth_style="bearer", key=KEY)
    assert seen == []
    assert KEY not in str(info.value)


async def test_probe_swallows_arbitrary_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"weird {KEY} {request.headers}")

    _wire(monkeypatch, boom)
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert KEY not in repr(out)


async def test_probe_invalid_url_inside_httpx_is_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.InvalidURL(f"bad {KEY}")

    _wire(monkeypatch, boom)
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert KEY not in repr(out)


async def test_probe_error_is_at_most_300_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(500, text="y" * 5000))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert len(out["error"]) <= 300


async def test_probe_key_with_bearer_prefix_is_not_doubled_and_token_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(401, text=f"bad token {KEY}"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=f"bEaReR {KEY}")
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in repr(out)


async def test_probe_redacts_url_encoded_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = "sk/abc+def=="
    _wire(monkeypatch, lambda r: httpx.Response(401, text="echo sk%2Fabc%2Bdef%3D%3D"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=key)
    assert "sk%2Fabc" not in out["error"]
    assert "***" in out["error"]


async def test_probe_caps_compressed_body_by_decoded_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gzip

    bomb = gzip.compress(b"0" * (registry_mod.PROBE_MAX_BODY_BYTES * 4))
    assert len(bomb) < registry_mod.PROBE_MAX_BODY_BYTES
    _wire(
        monkeypatch,
        lambda r: httpx.Response(
            200, stream=httpx.ByteStream(bomb), headers={"content-encoding": "gzip"}
        ),
    )
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert "too large" in out["error"]


async def test_probe_accepts_gzip_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import gzip

    body = gzip.compress(b'{"data": [{"id": "m1"}]}')
    seen = _wire(
        monkeypatch,
        lambda r: httpx.Response(
            200, stream=httpx.ByteStream(body), headers={"content-encoding": "gzip"}
        ),
    )
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is True
    assert seen[0].headers["accept-encoding"] == "identity"


# ── re-review: structural guard and decode edge cases ────────────────────────


async def test_probe_deeply_nested_json_is_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(200, content=b"[" * 100000))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert KEY not in repr(out)


async def test_probe_any_failure_after_the_request_is_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))

    def boom(text: str) -> Any:
        raise KeyError(f"surprise {KEY}")

    monkeypatch.setattr(registry_mod, "_parse_json", boom)
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key=KEY)
    assert out["ok"] is False
    assert out["payload"] is None
    assert KEY not in repr(out)


@pytest.mark.parametrize("style", ["header", "anthropic", "none"])
async def test_probe_strips_bearer_prefix_for_every_style(
    monkeypatch: pytest.MonkeyPatch, style: str
) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(401, text="echo sk-bare"))
    out = await UpstreamRegistry().probe(
        url=URL, auth_style=style, auth_header="X-Token", key="Bearer sk-bare"
    )
    assert "sk-bare" not in out["error"]
    sent = " ".join(seen[0].headers.values())
    assert "Bearer sk-bare" not in sent


async def test_probe_header_style_sends_bare_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _wire(monkeypatch, lambda r: httpx.Response(200, json={"data": []}))
    await UpstreamRegistry().probe(
        url=URL, auth_style="header", auth_header="X-Token", key="Bearer sk-bare"
    )
    assert seen[0].headers["x-token"] == "sk-bare"


def test_decode_capped_truncated_gzip_fails() -> None:
    import gzip

    whole = gzip.compress(b'{"data": [' + b"1," * 5000 + b"2]}")
    assert registry_mod._decode_capped(whole[: len(whole) // 2], "gzip") is None
    assert registry_mod._decode_capped(whole, "gzip") is not None


def test_decode_capped_ignores_trailing_data_after_eof() -> None:
    import gzip

    body = gzip.compress(b"hello") + b"trailing junk"
    assert registry_mod._decode_capped(body, "gzip") == b"hello"


def test_decode_capped_deflate_zlib_and_raw() -> None:
    import zlib

    payload = b'{"data": []}'
    assert registry_mod._decode_capped(zlib.compress(payload), "deflate") == payload
    co = zlib.compressobj(wbits=-15)
    raw = co.compress(payload) + co.flush()
    assert registry_mod._decode_capped(raw, "deflate") == payload


def test_decode_capped_raw_deflate_bomb_is_bounded() -> None:
    import zlib

    co = zlib.compressobj(wbits=-15)
    raw = co.compress(b"0" * (registry_mod.PROBE_MAX_BODY_BYTES * 4)) + co.flush()
    assert registry_mod._decode_capped(raw, "deflate") is None


async def test_probe_redacts_lowercase_percent_escapes(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, lambda r: httpx.Response(401, text="echo sk%2fabc%2bdef%3d%3d"))
    out = await UpstreamRegistry().probe(url=URL, auth_style="bearer", key="sk/abc+def==")
    assert "sk%2f" not in out["error"].lower()
    assert "***" in out["error"]
