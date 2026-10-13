"""Loopback httpx clients never go through a proxy (#2526).

hal0-api's Hindsight client, the Hermes core client and the Hermes plugins use
httpx, whose default ``trust_env=True`` sends a request to ``HTTP_PROXY`` /
``ALL_PROXY``. For the loopback memory engine that would hand the bearer and
the memory content to the proxy in cleartext.

Each test runs a real fake engine and a real fake proxy listener with the proxy
environment pointing at the proxy, and asserts the request reaches the engine
and the proxy sees nothing.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from hal0.agents.hermes.core.client import Hal0HermesClient
from hal0.agents.hermes.plugins.memory_hindsight import _client as plugin_memory
from hal0.agents.hermes.plugins.provider_hal0 import profile as plugin_profile
from hal0.http_client import loopback_client_kwargs
from hal0.memory.hindsight_client import HindsightRestClient, probe_health
from hal0.realtime import backends


class _Listener:
    """A real HTTP listener recording ``(method, path, Authorization)``."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                outer.seen.append((self.command, self.path, self.headers.get("Authorization")))
                raw = json.dumps({"status": "ok", "items": [], "data": [], "text": "hi"}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = _handle

            def log_message(self, *a: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(params=["HTTP_PROXY", "ALL_PROXY"])
def proxied(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """(engine, proxy), the proxy environment pointing at the proxy and
    NO_PROXY unset."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.delenv("REQUEST_METHOD", raising=False)
    engine, proxy = _Listener(), _Listener()
    monkeypatch.setenv(request.param, proxy.url)
    try:
        yield engine, proxy
    finally:
        engine.close()
        proxy.close()


def test_hindsight_probe_bypasses_the_proxy(proxied: Any) -> None:
    engine, proxy = proxied
    probe_health(base_url=engine.url, api_key="tenant-key")

    assert engine.seen == [("GET", "/health", "Bearer tenant-key")]
    assert proxy.seen == []


def test_hindsight_rest_client_bypasses_the_proxy(proxied: Any) -> None:
    engine, proxy = proxied

    async def run() -> None:
        client = HindsightRestClient(base_url=engine.url, api_key="tenant-key")
        try:
            await client.list_memories(bank_id="b")
        finally:
            await client._http.aclose()

    asyncio.run(run())

    assert [(m, a) for m, _, a in engine.seen] == [("GET", "Bearer tenant-key")]
    assert proxy.seen == []


def test_hermes_core_client_bypasses_the_proxy(proxied: Any) -> None:
    engine, proxy = proxied
    with Hal0HermesClient(engine.url, client_key="ck", admin_key="ak") as client:
        assert client.health().status_code == 200
        assert client.request_read("GET", "/api/slots").status_code == 200

    assert [(m, p, a) for m, p, a in engine.seen] == [
        ("GET", "/api/health", None),
        ("GET", "/api/slots", "Bearer ck"),
    ]
    assert proxy.seen == []


def test_hermes_memory_plugin_bypasses_the_proxy(proxied: Any) -> None:
    engine, proxy = proxied
    client = plugin_memory.Hal0MemoryClient(base_url=engine.url)
    try:
        client.search("q")
    finally:
        client.close()

    assert [(m, p) for m, p, _ in engine.seen] == [("POST", "/api/memory/search")]
    assert proxy.seen == []


def test_hermes_provider_plugin_bypasses_the_proxy(proxied: Any) -> None:
    engine, proxy = proxied
    profile = plugin_profile.Hal0ProviderProfile(base_url=f"{engine.url}/v1")
    profile.fetch_models(api_key="k")

    assert [(m, p) for m, p, _ in engine.seen] == [("GET", "/v1/models")]
    assert proxy.seen == []


def test_realtime_backend_self_call_bypasses_the_proxy(
    proxied: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, proxy = proxied
    monkeypatch.setattr(backends, "_self_base_url", lambda: engine.url)

    text = asyncio.run(backends._default_transcribe(b"RIFF", model="m", auth="Bearer k"))

    assert text == "hi"
    assert engine.seen == [("POST", "/v1/audio/transcriptions", "Bearer k")]
    assert proxy.seen == []


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:9177", "http://localhost:8080", "http://[::1]:8080/x"]
)
def test_loopback_kwargs_ignore_the_environment(url: str) -> None:
    kwargs = loopback_client_kwargs(url)
    assert kwargs["trust_env"] is False
    assert "verify" in kwargs


@pytest.mark.parametrize("url", ["https://api.example.com", "http://10.0.0.5:8080", "", "nonsense"])
def test_remote_kwargs_keep_the_environment(url: str) -> None:
    assert loopback_client_kwargs(url) == {}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:9177", False),
        ("http://localhost", False),
        ("http://[::1]:1", False),
        ("http://evil@127.0.0.1:8080", True),
        ("https://api.example.com", True),
        ("http://10.0.0.5", True),
        ("", True),
    ],
)
def test_plugin_stdlib_host_check_agrees_with_the_shared_helper(url: str, expected: bool) -> None:
    for fn in (plugin_memory._loopback_client_kwargs, plugin_profile._loopback_client_kwargs):
        assert (fn(url).get("trust_env", True)) is expected
    assert (loopback_client_kwargs(url).get("trust_env", True)) is expected


def test_profile_headline_self_call_bypasses_the_proxy(proxied: Any) -> None:
    """The utility-LLM self-call sends the caller's or the box's Authorization."""
    from hal0.profiles import generate

    engine, proxy = proxied
    facts = generate._ModelFacts(
        display_name="m",
        capability="chat",
        architecture="llama",
        quant="q4",
        context_length=4096,
        size_bytes=1,
        mtp_eligible=False,
        tags=(),
        id_for_moe="m",
        card_text="",
    )
    llm = generate.LlmCallContext(base_url=engine.url, headers={"Authorization": "Bearer k"})

    asyncio.run(generate._llm_headline(facts, llm))

    assert engine.seen == [("POST", "/v1/chat/completions", "Bearer k")]
    assert proxy.seen == []
