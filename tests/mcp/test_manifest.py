"""Unit tests for :mod:`hal0.mcp.manifest` — #224 install-from-URL resolver."""

from __future__ import annotations

from typing import Any

import pytest

from hal0.errors import BadRequest
from hal0.mcp import manifest


@pytest.mark.asyncio
async def test_resolve_oci_synthesises_id_and_name() -> None:
    r = await manifest.resolve("oci://ghcr.io/example/mcp-tools:latest")
    assert r.id == "mcp-tools"
    assert r.name == "mcp-tools"
    assert r.spec == "oci://ghcr.io/example/mcp-tools:latest"
    assert r.source_kind == "oci"
    assert r.transport == "streamable-http"


@pytest.mark.asyncio
async def test_resolve_npm_strips_scope_for_name() -> None:
    r = await manifest.resolve("npm:@some-org/mcp-things")
    assert r.id == "mcp-things"
    assert r.name == "mcp-things"
    assert r.source_kind == "npm"


@pytest.mark.asyncio
async def test_resolve_uvx_keeps_pkg_name() -> None:
    r = await manifest.resolve("uvx:mcp-server-filesystem")
    assert r.id == "mcp-server-filesystem"
    assert r.name == "mcp-server-filesystem"
    assert r.source_kind == "uvx"


@pytest.mark.asyncio
async def test_resolve_git_https_uses_repo_name() -> None:
    r = await manifest.resolve("git+https://github.com/example/mcp-things")
    assert r.id == "mcp-things"
    assert r.name == "mcp-things"
    assert r.source_kind == "git"


@pytest.mark.asyncio
async def test_resolve_git_https_with_trailing_dot_git() -> None:
    r = await manifest.resolve("git+https://github.com/example/mcp-things.git")
    assert r.id == "mcp-things"


@pytest.mark.asyncio
async def test_resolve_http_manifest_with_fake_fetcher() -> None:
    async def _fetch(url: str) -> dict[str, Any]:
        return {
            "name": "example-mcp",
            "description": "demo",
            "tools": 7,
            "transport": "streamable-http",
            "env": {"FOO": "bar"},
        }

    r = await manifest.resolve("https://example.com/mcp.json", fetcher=_fetch)
    assert r.name == "example-mcp"
    assert r.tools == 7
    assert r.transport == "streamable-http"
    assert r.env_required == ["FOO"]
    assert r.source_kind == "manifest"
    assert r.source_url == "https://example.com/mcp.json"


@pytest.mark.asyncio
async def test_resolve_http_falls_back_when_non_json() -> None:
    async def _fetch(url: str) -> Any:
        return None  # not a dict — synthesise from URL last segment

    r = await manifest.resolve("https://example.com/foo.json", fetcher=_fetch)
    assert r.id == "foo"
    assert r.name == "foo"
    assert r.source_kind == "http"


@pytest.mark.asyncio
async def test_resolve_http_tools_as_list_returns_length() -> None:
    async def _fetch(url: str) -> dict[str, Any]:
        return {
            "name": "many-tools",
            "tools": ["a", "b", "c"],
        }

    r = await manifest.resolve("https://example.com/m.json", fetcher=_fetch)
    assert r.tools == 3


@pytest.mark.asyncio
async def test_resolve_rejects_empty_url() -> None:
    with pytest.raises(BadRequest) as exc:
        await manifest.resolve("")
    assert exc.value.code == "mcp.url_required"


@pytest.mark.asyncio
async def test_resolve_rejects_unknown_scheme() -> None:
    with pytest.raises(BadRequest) as exc:
        await manifest.resolve("ftp://no")
    assert exc.value.code == "mcp.spec_unsupported"


@pytest.mark.asyncio
async def test_resolve_rejects_too_long_url() -> None:
    with pytest.raises(BadRequest) as exc:
        await manifest.resolve("https://x/" + "a" * 4096)
    assert exc.value.code == "mcp.url_too_long"


@pytest.mark.asyncio
async def test_resolve_propagates_fetch_failure_as_bad_request() -> None:
    import httpx

    async def _fetch(url: str) -> Any:
        raise httpx.ConnectError("nope")

    with pytest.raises(BadRequest) as exc:
        await manifest.resolve("https://example.com/mcp.json", fetcher=_fetch)
    assert exc.value.code == "mcp.manifest_fetch_failed"


# ── SSRF guard ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_blocks_localhost_url() -> None:
    """Loopback hostnames + 127.0.0.1 literals must short-circuit pre-fetch."""
    for url in (
        "http://localhost/manifest.json",
        "http://127.0.0.1/manifest.json",
        "http://127.0.0.1:9000/v1/load",
        "http://[::1]/manifest.json",
    ):
        with pytest.raises(BadRequest) as exc:
            await manifest.resolve(url)
        assert exc.value.code == "mcp.ssrf_blocked", f"missed SSRF guard for {url}"


@pytest.mark.asyncio
async def test_resolve_blocks_private_lan_url() -> None:
    """RFC 1918 ranges (10/8, 172.16/12, 192.168/16) must reject pre-fetch."""
    for url in (
        "http://10.0.1.142:8080/api/slots",
        "http://172.16.0.5/manifest.json",
        "http://192.168.1.1/manifest.json",
    ):
        with pytest.raises(BadRequest) as exc:
            await manifest.resolve(url)
        assert exc.value.code == "mcp.ssrf_blocked", f"missed SSRF guard for {url}"


@pytest.mark.asyncio
async def test_resolve_blocks_link_local_url() -> None:
    """Link-local (incl. AWS/GCP IMDS at 169.254.169.254) must reject."""
    for url in (
        "http://169.254.169.254/latest/meta-data/",  # AWS / GCP IMDS
        "http://169.254.1.1/",
    ):
        with pytest.raises(BadRequest) as exc:
            await manifest.resolve(url)
        assert exc.value.code == "mcp.ssrf_blocked", f"missed SSRF guard for {url}"


@pytest.mark.asyncio
async def test_resolve_blocks_mdns_local_hostname() -> None:
    """``*.local`` mDNS hostnames are rejected without a DNS lookup."""
    with pytest.raises(BadRequest) as exc:
        await manifest.resolve("http://hal0.local/manifest.json")
    assert exc.value.code == "mcp.ssrf_blocked"


@pytest.mark.asyncio
async def test_resolve_does_not_follow_redirect() -> None:
    """The default fetcher must run with ``follow_redirects=False``.

    A 30x to an internal host would otherwise bypass the pre-flight
    SSRF check. We assert the AsyncClient kwarg at construction time so
    a regression is caught without spinning up an HTTP server.
    """
    import inspect

    src = inspect.getsource(manifest._default_fetcher)
    assert "follow_redirects=False" in src, (
        "_default_fetcher must construct httpx.AsyncClient with follow_redirects=False"
    )


# ── Tools-count cap ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_caps_tools_count_at_4096() -> None:
    """A manifest with a runaway tools list must not 500 on Pydantic ``le``."""

    async def _fetch(url: str) -> dict[str, Any]:
        return {
            "name": "huge",
            "tools": ["t"] * 5000,
        }

    r = await manifest.resolve("https://example.com/m.json", fetcher=_fetch)
    assert r.tools == 4096


@pytest.mark.asyncio
async def test_default_fetcher_aborts_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """#381: the production fetcher streams and aborts once cumulative body
    bytes exceed _MAX_MANIFEST_BYTES, rather than buffering it all first."""
    import httpx

    monkeypatch.setattr(manifest, "_enforce_safe_url", lambda url: None)
    monkeypatch.setattr(manifest, "_MAX_MANIFEST_BYTES", 32)

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        async def aiter_bytes(self):
            yield b"x" * 20
            yield b"y" * 20  # cumulative 40 > 32 -> must abort here

    class _FakeStream:
        async def __aenter__(self):
            return _FakeResp()

        async def __aexit__(self, *a):
            return False

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return _FakeStream()

    monkeypatch.setattr(manifest.httpx, "AsyncClient", lambda *a, **k: _FakeClient())
    with pytest.raises(httpx.HTTPError, match="too large"):
        await manifest._default_fetcher("https://example.com/big.json")
