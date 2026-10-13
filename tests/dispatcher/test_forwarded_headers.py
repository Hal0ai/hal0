"""The dispatcher forwards an allowlist of client headers (#2538).

Before the fix ``Dispatcher._build_headers`` copied every client header except
the hop-by-hop set and ``Authorization``, so the ``hal0_session`` cookie (an
admin login), ``X-API-Key`` and ``X-hal0-*`` reached whichever upstream the
request resolved to, remote cloud providers included.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.requests import Request

from hal0.dispatcher.router import Dispatcher
from hal0.forwarded_headers import FORWARDABLE_REQUEST_HEADERS, forwardable_request_headers
from hal0.upstreams.registry import Upstream, UpstreamRegistry

_SECRET_HEADERS = {
    "cookie": "hal0_session=admin-session-do-not-leak",
    "authorization": "Bearer hal0-client-key-do-not-leak",
    "x-api-key": "hal0-api-key-do-not-leak",
    "x-hal0-agent": "hermes",
    "x-hal0-private": "1",
    "proxy-authorization": "Basic do-not-leak",
    "forwarded": "for=192.0.2.10",
    "x-forwarded-for": "192.0.2.10",
    "x-forwarded-host": "hal0.example",
    "x-real-ip": "192.0.2.10",
    "x-stainless-os": "Linux",
}

_SSE_BODY = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

_PROTOCOL_HEADERS = {
    "content-type": "application/json",
    "accept": "text/event-stream",
    "user-agent": "OpenAI/Python 1.0",
    "anthropic-beta": "prompt-caching-2024-07-31",
    "openai-organization": "org-test",
    "openai-project": "proj-test",
    "openai-beta": "assistants=v2",
}


class _Registry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)

    def __getitem__(self, name: str) -> Upstream:
        return self._store[name]


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "raw_path": b"/v1/chat/completions",
            "query_string": b"",
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "http_version": "1.1",
            "root_path": "",
        }
    )


async def _forward_and_capture(
    upstream: Upstream,
    model: str,
    *,
    stream: bool = False,
) -> tuple[httpx.Request, bytes]:
    """Dispatch + forward one request carrying every header, capture what leaves.

    Returns the request the upstream received and the body hal0 relayed back
    to the client (drained from the ``StreamingResponse`` when ``stream``).
    """
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if stream:
            return httpx.Response(
                200,
                stream=httpx.ByteStream(_SSE_BODY),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"ok": True})

    async def online(_u: Upstream) -> bool:
        return True

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = Dispatcher(
        upstream_registry=_Registry([upstream]),
        is_online=online,
        cached_models=lambda name: [model] if name == upstream.name else [],
        http_client=client,
    )
    try:
        request = _request({**_SECRET_HEADERS, **_PROTOCOL_HEADERS, "accept-encoding": "gzip"})
        body: dict[str, object] = {"model": model, "messages": []}
        if stream:
            body["stream"] = True
        call = await dispatcher.dispatch(request, body=body)
        assert call.streaming is stream
        response = await dispatcher.forward(call)
        if stream:
            relayed = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        else:
            relayed = bytes(response.body)
    finally:
        await client.aclose()
    assert response.status_code == 200
    assert len(seen) == 1
    return seen[0], relayed


def _cloud(monkeypatch: pytest.MonkeyPatch) -> Upstream:
    monkeypatch.setenv("TEST_CLOUD_KEY", "sk-upstream-key")
    return Upstream(
        name="cloud",
        kind="remote",
        url="https://api.cloud.example/v1",
        auth_value_env="TEST_CLOUD_KEY",
    )


def _assert_no_client_credentials(sent: httpx.Request) -> None:
    for name in _SECRET_HEADERS:
        if name == "authorization":
            continue
        assert name not in sent.headers, f"{name} leaked to the upstream"
    leaked = [v for v in sent.headers.values() if "do-not-leak" in v]
    assert leaked == []
    assert not any(k.lower().startswith("x-hal0-") for k in sent.headers)


@pytest.mark.asyncio
async def test_client_credentials_never_reach_a_remote_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The #2538 regression: an admin's browser request to a cloud upstream.

    The request carries the ``hal0_session`` cookie, a hal0 bearer, an
    ``X-API-Key`` and ``X-hal0-*``; the provider sees none of them, only the
    upstream's own key.
    """
    sent, _ = await _forward_and_capture(_cloud(monkeypatch), "cloud-model")

    _assert_no_client_credentials(sent)
    # Upstream auth comes from the upstream's own credential, not the client's.
    assert sent.headers.get_list("authorization") == ["Bearer sk-upstream-key"]


@pytest.mark.asyncio
async def test_protocol_headers_still_reach_a_remote_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent, _ = await _forward_and_capture(_cloud(monkeypatch), "cloud-model")

    for name, value in _PROTOCOL_HEADERS.items():
        assert sent.headers.get(name) == value, name
    # The stream relay passes raw bytes on and drops content-encoding, so the
    # dispatcher asks for an unencoded body rather than copying the client's.
    assert sent.headers.get("accept-encoding") == "identity"


@pytest.mark.asyncio
async def test_anthropic_upstream_keeps_its_own_version_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-ant-upstream")
    remote = Upstream(
        name="anthropic",
        kind="remote",
        url="https://api.anthropic.example/v1",
        auth_style="anthropic",
        auth_value_env="TEST_ANTHROPIC_KEY",
    )

    sent, _ = await _forward_and_capture(remote, "claude-test")

    assert sent.headers.get_list("x-api-key") == ["sk-ant-upstream"]
    assert sent.headers.get("anthropic-version") == "2023-06-01"
    assert sent.headers.get("anthropic-beta") == _PROTOCOL_HEADERS["anthropic-beta"]
    assert "cookie" not in sent.headers


@pytest.mark.asyncio
async def test_local_slot_gets_what_it_needs_and_nothing_else() -> None:
    slot = Upstream(
        name="chat",
        kind="slot",
        url="http://127.0.0.1:8081/v1",
        auth_style="none",
        slot_name="chat",
    )

    sent, _ = await _forward_and_capture(slot, "qwen3-4b")

    assert str(sent.url) == "http://127.0.0.1:8081/v1/chat/completions"
    assert sent.headers.get("content-type") == "application/json"
    assert sent.headers.get("accept") == "text/event-stream"
    assert json.loads(sent.content)["model"] == "qwen3-4b"
    for name in _SECRET_HEADERS:
        assert name not in sent.headers, f"{name} leaked to the slot"


@pytest.mark.asyncio
async def test_streaming_request_relays_and_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent, relayed = await _forward_and_capture(_cloud(monkeypatch), "cloud-model", stream=True)

    _assert_no_client_credentials(sent)
    assert sent.headers.get_list("authorization") == ["Bearer sk-upstream-key"]
    assert sent.headers.get("accept") == "text/event-stream"
    assert sent.headers.get("accept-encoding") == "identity"
    assert json.loads(sent.content)["stream"] is True
    assert relayed == _SSE_BODY


# ── the allowlist itself ─────────────────────────────────────────────────


def test_allowlist_matches_names_case_insensitively_and_lowercases_keys() -> None:
    out = forwardable_request_headers(
        {"Content-Type": "application/json", "COOKIE": "hal0_session=x", "Anthropic-Beta": "b"}
    )
    assert out == {"content-type": "application/json", "anthropic-beta": "b"}


def test_extra_widens_the_list_for_one_call_only() -> None:
    headers = {"If-None-Match": '"e"', "X-API-Key": "k"}
    assert forwardable_request_headers(headers) == {}
    assert forwardable_request_headers(headers, extra=("If-None-Match",)) == {
        "if-none-match": '"e"'
    }


@pytest.mark.parametrize(
    "name",
    [
        "cookie",
        "authorization",
        "x-api-key",
        "proxy-authorization",
        "x-hal0-agent",
        "x-hal0-private",
        "x-hermes-session-token",
        "forwarded",
        "x-forwarded-for",
        "host",
        "content-length",
        "accept-encoding",
    ],
)
def test_credentials_and_hop_headers_are_not_on_the_allowlist(name: str) -> None:
    assert name not in FORWARDABLE_REQUEST_HEADERS
    assert not any(h.startswith(("x-hal0-", "proxy-")) for h in FORWARDABLE_REQUEST_HEADERS)
