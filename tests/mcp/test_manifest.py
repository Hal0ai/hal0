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
